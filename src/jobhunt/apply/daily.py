import logging
from collections.abc import Callable
from datetime import datetime

from sqlmodel import Session, select

from jobhunt.apply.digest import build_digest
from jobhunt.apply.followup import mark_cold
from jobhunt.apply.queue import tailor_shortlist
from jobhunt.cli import summarize_scan
from jobhunt.config import Config
from jobhunt.jobs.models import Job, JobFacts, Score
from jobhunt.scan import ScanResult
from jobhunt.state import is_paused
from jobhunt.telegram import TelegramClient, queue_message

log = logging.getLogger(__name__)


def _send_queued(session: Session, client: TelegramClient, application, web_url: str) -> None:
    job = session.get(Job, application.job_id)
    score = session.exec(
        select(Score).where(Score.job_id == job.id, Score.content_hash == job.content_hash)
    ).first()
    text, buttons = queue_message(application, job, score, session.get(JobFacts, job.id), web_url)
    client.send(text, buttons)


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
        if not is_paused(session):
            try:
                run = tailor_shortlist(session, llm, config)
                stopped = run.stopped
                if client:
                    for application in run.queued:
                        _send_queued(session, client, application, config.autopilot.web_url)
            except Exception as exc:
                log.warning("daily tailoring failed", exc_info=True)
                session.rollback()
                stopped = f"Tailoring failed: {type(exc).__name__}"
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
