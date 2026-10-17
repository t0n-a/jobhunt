import logging
from collections.abc import Callable
from datetime import datetime

import httpx
from sqlmodel import Session, select

from jobhunt.answers import load_answers
from jobhunt.apply.autopilot import autosubmit
from jobhunt.apply.digest import build_digest
from jobhunt.apply.easy_apply import easy_apply_brief, is_linkedin_job
from jobhunt.apply.followup import mark_cold
from jobhunt.apply.prepare import prepare_plan
from jobhunt.apply.queue import tailor_shortlist
from jobhunt.cli import summarize_scan
from jobhunt.config import Config
from jobhunt.growth.gh import Gh, GhError
from jobhunt.growth.models import Suggestion
from jobhunt.growth.readme import draft_profile_readme, draft_repo_readme
from jobhunt.growth.suggest import _find, refresh
from jobhunt.jobs.models import Job, JobFacts, Score
from jobhunt.outreach.log import draft_followups, draft_for_run
from jobhunt.outreach.models import Contact
from jobhunt.profile.store import AtomStore
from jobhunt.scan import ScanResult
from jobhunt.state import is_paused
from jobhunt.tailor.models import ApplicationEvent
from jobhunt.telegram import TelegramClient, growth_message, outreach_message, queue_message

log = logging.getLogger(__name__)


def _load_answers(config: Config):
    try:
        return load_answers(config.answers_path)
    except ValueError:
        log.warning("answers file unreadable; the Easy Apply brief omits answers", exc_info=True)
        return None


def _send_queued(
    session: Session, client: TelegramClient, application, web_url: str, answers
) -> None:
    last = session.exec(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application.id)
        .order_by(ApplicationEvent.id.desc())
    ).first()
    # "autopilot stopped: ..." and "autopilot error: ..."; "autopilot: submitting" is not for the candidate.
    note = last.note if last and last.note.startswith("autopilot ") else ""
    job = session.get(Job, application.job_id)
    score = session.exec(
        select(Score).where(Score.job_id == job.id, Score.content_hash == job.content_hash)
    ).first()
    # Easy Apply jobs: the candidate applies himself, so the brief carries the CV link and answers.
    extra = easy_apply_brief(application, job, answers, web_url) if is_linkedin_job(job.url) else ""
    text, buttons = queue_message(
        application, job, score, session.get(JobFacts, job.id), web_url, note, extra
    )
    client.send(text, buttons)


def _send_outreach(session: Session, client: TelegramClient, outreach, web_url: str) -> None:
    job, contact = session.get(Job, outreach.job_id), session.get(Contact, outreach.contact_id)
    if job is None or contact is None:
        return
    for i, (text, buttons) in enumerate(outreach_message(outreach, job, contact, web_url)):
        if not client.send(text, buttons) and i == 0 and buttons:
            # Some clients or API versions reject copy_text; the Sent/Skip row still works.
            client.send(text, [r for r in buttons if not any("copy_text" in b for b in r)])


def _grow(session: Session, config: Config, llm, client, now: datetime) -> None:
    user = config.profile.github_username
    try:
        gh = Gh()
        new = refresh(session, gh, config, now=now, workdir=config.data_dir / "growth_audit")
        if llm is not None:
            atoms = AtomStore(session).confirmed_only()
            approved = session.exec(
                select(Suggestion).where(Suggestion.kind == "publish", Suggestion.status == "approved")
            ).all()
            for p in approved:
                if _find(session, "readme", p.target) is None:
                    new.append(draft_repo_readme(session, gh, p.target, atoms, llm))
            if _find(session, "profile_readme", f"{user}/{user}") is None:
                new.append(draft_profile_readme(session, gh, user, atoms, gh.repos(user, "public"), llm))
    except GhError as e:  # gh missing or logged out: skip growth, keep going
        log.warning("growth skipped: %s", e)
        return
    if client:
        for s in new:
            if s is not None:
                client.send(growth_message(s))


def run_daily(
    config: Config,
    *,
    engine,
    llm,
    client: TelegramClient | None,
    scan: Callable[[], ScanResult | None],
    now: datetime,
) -> str:
    try:
        result = scan()
        scan_line = summarize_scan(result)
    except Exception as exc:  # noqa: BLE001 - a broken source must not stop tailoring or the digest
        log.warning("daily scan failed: %s", exc)
        result, scan_line = None, f"Scan failed: {exc}"
    linkedin = bool(result) and "linkedin" in result.failed_sources + result.timed_out_sources

    with Session(engine) as session:
        ghosted = mark_cold(session, now)
        stopped = None
        if llm is None:
            stopped = "No LLM key; tailoring skipped. Run jobhunt setup."
        elif not is_paused(session):
            try:
                run = tailor_shortlist(session, llm, config)
                stopped = run.stopped
                with httpx.Client(timeout=15) as http:
                    for application in run.queued:
                        try:
                            if is_linkedin_job(session.get(Job, application.job_id).url):
                                continue  # Easy Apply: no LinkedIn page is fetched
                            prepare_plan(session, application, config, llm, http)
                        except Exception:  # one unreadable form must not stop the run
                            log.warning("form plan failed for #%s", application.id, exc_info=True)
                            session.rollback()
                if not is_paused(session):
                    try:
                        autosubmit(session, config, now=now)
                    except Exception:  # the queue messages below must still go out
                        log.warning("autosubmit failed", exc_info=True)
                        session.rollback()
                if client:
                    answers = _load_answers(config)
                    for application in run.queued:
                        session.refresh(application)
                        if application.status != "queued":
                            continue
                        _send_queued(session, client, application, config.autopilot.web_url, answers)
            except Exception as exc:
                log.warning("daily tailoring failed", exc_info=True)
                session.rollback()
                stopped = f"Tailoring failed: {type(exc).__name__}"
        if llm is not None and not is_paused(session):
            try:
                new = draft_for_run(session, config, llm) + draft_followups(session, config, llm, now)
                if client:
                    for outreach in new:
                        _send_outreach(session, client, outreach, config.autopilot.web_url)
            except Exception:  # the digest must still go out
                log.warning("daily outreach failed", exc_info=True)
                session.rollback()
        if not is_paused(session) and config.profile.github_username:
            try:
                _grow(session, config, llm, client, now)
            except Exception:  # the digest must still go out
                log.warning("daily growth failed", exc_info=True)
                session.rollback()
        lines = build_digest(
            session,
            now=now,
            scan_line=scan_line,
            linkedin_skipped=linkedin,
            ghosted=ghosted,
            config=config,
        ).split("\n")
    if stopped:
        i = next(i for i, x in enumerate(lines) if x.startswith("Scan:"))
        lines.insert(i + 1, f"Tailoring stopped: {stopped}")
    digest = "\n".join(lines)
    if client:
        client.send(digest)
    return digest
