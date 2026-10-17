"""Autopilot: submit in the candidate's name only when every rule holds. When in doubt, don't."""

import logging
from datetime import datetime
from urllib.parse import urlsplit

from sqlmodel import Session, select

from jobhunt.apply import browser
from jobhunt.apply.ats import AtsTarget, detect_ats
from jobhunt.apply.plan import FormPlan
from jobhunt.apply.queue import autopilot_available, trust_count
from jobhunt.apply.tracker import set_status
from jobhunt.config import AutopilotConfig, Config
from jobhunt.jobs.models import Job, Score
from jobhunt.state import block_host, get_state, is_blocked, is_paused, set_state
from jobhunt.tailor.models import Application, ApplicationEvent

log = logging.getLogger(__name__)

# A failed click may still have submitted: the host stays blocked rather than risk a duplicate.
NO_CONFIRMATION = "autopilot error: no confirmation - it may have gone through; check before applying again"


def autopilot_on(session: Session) -> bool:
    return get_state(session, "autopilot", "0") == "1"


def set_autopilot(session: Session, on: bool, cfg: AutopilotConfig) -> str:
    if not on:
        set_state(session, "autopilot", "0")
        return "Autopilot is off."
    if not autopilot_available(session, cfg):
        return (
            f"Not yet: autopilot needs {cfg.trust_target} clean approvals; "
            f"you have {trust_count(session)}."
        )
    set_state(session, "autopilot", "1")
    return (
        f"Autopilot is on. jobhunt will submit up to {cfg.daily_cap} applications a day in your "
        "name, only on forms with no login and no captcha, for jobs with fit "
        f'{cfg.autopilot_fit} or more. Send "autopilot off" to stop.'
    )


def sent_today(session: Session, now: datetime) -> int:
    """Autopilot sends on `now`'s UTC date."""
    count = 0
    for event in session.exec(select(ApplicationEvent).where(ApplicationEvent.kind == "sent")):
        application = session.get(Application, event.application_id)
        if (
            application
            and application.channel == "autopilot"
            and event.at.date() == now.date()  # stored UTC; naive after sqlite
        ):
            count += 1
    return count


def gate(
    session: Session,
    application: Application,
    *,
    job: Job,
    score: Score | None,
    plan: FormPlan | None,
    target: AtsTarget | None,
    cfg: AutopilotConfig,
    now: datetime,
) -> str | None:
    if not autopilot_on(session):
        return "autopilot off"
    if is_paused(session):
        return "paused"
    if not autopilot_available(session, cfg):
        return "trust not earned"
    if application.status != "queued":
        return "not queued"
    # One attempt per application: a form that stopped once (captcha, login) would stop again,
    # and reloading it every day looks like a bot. It stays in the candidate's queue instead.
    if session.exec(
        select(ApplicationEvent).where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.note.startswith("autopilot"),
        )
    ).first():
        return "autopilot already tried this form"
    if score is None or score.fit_score is None or score.fit_score < cfg.autopilot_fit:
        return f"fit {'?' if score is None or score.fit_score is None else score.fit_score} < {cfg.autopilot_fit}"
    if target is None or target.kind not in cfg.ats_allowlist:
        return "unsupported form"
    host = urlsplit(target.apply_url).hostname
    if not host or is_blocked(session, host):
        return "blocked host"
    if plan is None or not plan.complete:
        return "form not fully answered"
    if sent_today(session, now) >= cfg.daily_cap:
        return "daily cap reached"
    return None


def autosubmit(
    session: Session, config: Config, *, runner=browser.run_form, now: datetime
) -> list[tuple[Application, browser.FormRun]]:
    cfg = config.autopilot
    done = []
    queued = session.exec(select(Application).where(Application.status == "queued")).all()
    for application in queued:
        job = session.get(Job, application.job_id)
        score = session.exec(
            select(Score).where(Score.job_id == job.id, Score.content_hash == job.content_hash)
        ).first()
        plan = FormPlan.from_json(application.form_plan) if application.form_plan else None
        target = detect_ats(job.url)
        if gate(
            session, application, job=job, score=score, plan=plan, target=target, cfg=cfg, now=now
        ):
            continue
        host = urlsplit(target.apply_url).hostname
        application.channel = "autopilot"
        set_status(session, application, "approved", note="autopilot", now=now)
        try:
            run = runner(
                target.apply_url,
                plan,
                headless=True,
                submit=True,
                allow_obstacles=False,
                screenshot_dir=config.data_dir / "screenshots",
            )
        except Exception as exc:
            log.warning("autopilot run failed for #%s", application.id, exc_info=True)
            run = browser.FormRun("error", f"{type(exc).__name__}")
        if run.status == "submitted":
            set_status(session, application, "sent", note="autopilot", now=now)
        elif run.status == "stopped":
            set_status(
                session, application, "queued", note=f"autopilot stopped: {run.reason}", now=now
            )
        else:
            block_host(session, host, run.reason)
            note = (
                NO_CONFIRMATION
                if "no confirmation" in run.reason
                else f"autopilot error: {run.reason}"
            )
            set_status(session, application, "queued", note=note, now=now)
        done.append((application, run))
    return done
