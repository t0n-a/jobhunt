"""The approval queue: auto-tailor the shortlist, approve or skip, count trust."""

import logging
from dataclasses import dataclass, field

from sqlmodel import Session, select

from jobhunt.apply.tracker import set_status
from jobhunt.config import AutopilotConfig, Config
from jobhunt.jobs.dedupe import normalize, role_match
from jobhunt.jobs.models import Job
from jobhunt.llm import LLMError
from jobhunt.matching.score import EmptyProfileError
from jobhunt.profile.store import AtomStore
from jobhunt.ranking.shortlist import shortlist
from jobhunt.tailor.models import Application, iter_bullets
from jobhunt.tailor.pipeline import render_application, tailor_job

log = logging.getLogger(__name__)


@dataclass
class TailorRun:
    queued: list[Application] = field(default_factory=list)
    failed: int = 0
    stopped: str | None = None


def tailor_shortlist(session: Session, llm, config: Config) -> TailorRun:
    run = TailorRun(queued=[])
    if not AtomStore(session).confirmed_only():
        run.stopped = (
            "No confirmed evidence yet. Confirm some facts at /atoms, then tailoring starts."
        )
        return run
    taken = [
        (normalize(job.company), job.title)
        for job in session.exec(select(Job).join(Application, Application.job_id == Job.id)).all()
    ]
    for ranked in shortlist(session, config.ranking):
        if len(run.queued) >= config.autopilot.tailor_per_run:
            break
        job = ranked.job
        company = normalize(job.company)
        if any(c == company and role_match(t, job.title) for c, t in taken):
            continue
        try:
            application = tailor_job(session, job, llm, profile=config.profile)
        except EmptyProfileError:  # subclass of LLMError: must come first
            # This job matches none of the confirmed evidence; the next one may.
            run.failed += 1
            continue
        except LLMError:
            run.failed += 1
            continue
        except Exception:
            log.warning("tailoring failed for job %s", job.id, exc_info=True)
            session.rollback()
            run.failed += 1
            continue
        try:
            application = render_application(
                session, application, profile=config.profile, data_dir=config.data_dir
            )
        except Exception:
            log.warning("render failed for application %s", application.id, exc_info=True)
        application.bullets_at_draft = len(iter_bullets(application.resume_json))
        set_status(session, application, "queued")
        taken.append((company, job.title))
        run.queued.append(application)
    return run


def edit_count(application: Application) -> int:
    """Bullets the candidate rewrote plus bullets they deleted since the draft."""
    if application.bullets_at_draft is None:
        return 0
    bullets = iter_bullets(application.resume_json)
    edited = sum(1 for _, b in bullets if b.get("source") == "user")
    return edited + max(0, application.bullets_at_draft - len(bullets))


def _require_queued(application: Application) -> None:
    if application.status != "queued":
        raise ValueError(f"#{application.id} is {application.status}")


def approve(session: Session, application: Application, cfg: AutopilotConfig) -> bool:
    _require_queued(application)
    application.trust_credit = edit_count(application) <= cfg.heavy_edit_limit
    set_status(session, application, "approved")
    return application.trust_credit


def skip(session: Session, application: Application) -> None:
    _require_queued(application)
    set_status(session, application, "skipped")


def trust_count(session: Session) -> int:
    return len(session.exec(select(Application).where(Application.trust_credit)).all())


def autopilot_available(session: Session, cfg: AutopilotConfig) -> bool:
    return trust_count(session) >= cfg.trust_target
