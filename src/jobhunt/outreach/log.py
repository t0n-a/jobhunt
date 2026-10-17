"""Who gets an outreach draft, the stored drafts, their status, and which follow-ups are due."""

import logging
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from jobhunt.config import Config
from jobhunt.jobs.dedupe import normalize
from jobhunt.jobs.models import Job
from jobhunt.llm import LLMProvider
from jobhunt.outreach.contacts import contact_for
from jobhunt.outreach.draft import draft_followup, draft_outreach
from jobhunt.outreach.models import Contact, Outreach
from jobhunt.profile.store import AtomStore
from jobhunt.ranking.shortlist import shortlist
from jobhunt.state import is_paused
from jobhunt.tailor.models import Application

log = logging.getLogger(__name__)

# An application in any of these is live; a rejected or withdrawn one is not worth a recruiter's time.
LIVE_STATUSES = ("queued", "approved", "sent")
OPEN = ("drafted", "sent")
FIRST_KINDS = ("message", "connection_note")
# status -> statuses it may move to
TRANSITIONS = {"drafted": ("sent", "skipped"), "sent": ("replied",)}


def _commit(session: Session) -> None:
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite hands back naive datetimes


def _open_companies(session: Session) -> set[str]:
    rows = session.exec(
        select(Job.company).join(Outreach, Outreach.job_id == Job.id).where(Outreach.status.in_(OPEN))
    ).all()
    return {normalize(c) for c in rows}


def _contacted(session: Session, contact: Contact) -> bool:
    # Any status: a skipped draft still means the candidate decided about this person.
    return (
        session.exec(
            select(Outreach.id).where(
                Outreach.contact_id == contact.id, Outreach.kind.in_(FIRST_KINDS)
            )
        ).first()
        is not None
    )


def _eligible(session: Session, job: Job) -> Contact | None:
    if normalize(job.company) in _open_companies(session):
        return None
    contact = contact_for(session, job)
    return None if _contacted(session, contact) else contact


def _strong(session: Session, config: Config) -> list[Job]:
    min_fit = config.outreach.min_fit or config.ranking.strong_fit
    jobs = []
    for r in shortlist(session, config.ranking):
        if r.score.fit_score < min_fit:
            continue
        app = session.exec(select(Application).where(Application.job_id == r.job.id)).first()
        if app is None or app.status in LIVE_STATUSES:
            jobs.append(r.job)
    return jobs


def candidates(session: Session, config: Config) -> list[Job]:
    out, companies = [], set()
    for job in _strong(session, config):
        company = normalize(job.company)
        if company in companies or _eligible(session, job) is None:
            continue
        companies.add(company)  # one draft per company per run
        out.append(job)
    return out


def draft_for_run(session: Session, config: Config, llm: LLMProvider) -> list[Outreach]:
    if is_paused(session):
        return []
    atoms = AtomStore(session).confirmed_only()
    if not atoms:
        return []
    stored: list[Outreach] = []
    for job in candidates(session, config)[: config.outreach.per_run]:
        contact = _eligible(session, job)  # re-check: earlier drafts this run may have used it
        if contact is None:
            continue
        result = draft_outreach(job, contact, atoms, llm, profile=config.profile)
        if not result.drafts:
            log.warning("outreach: no usable draft for job %s (%s); retry next run", job.id, result.dropped)
            continue
        for d in result.drafts:
            row = Outreach(
                job_id=job.id, contact_id=contact.id, kind=d.kind, text=d.text, atom_ids=list(d.atom_ids)
            )
            session.add(row)
            stored.append(row)
        _commit(session)
    return stored


def mark(session: Session, outreach_id: int, status: str, now: datetime) -> Outreach:
    row = session.get(Outreach, outreach_id)
    if row is None:
        raise ValueError(f"no outreach {outreach_id}")
    if status not in TRANSITIONS.get(row.status, ()):
        raise ValueError(f"cannot move outreach from {row.status} to {status}")
    row.status = status
    if status == "sent":
        row.sent_at = now
    elif status == "replied":
        row.replied_at = now
    session.add(row)
    _commit(session)
    return row


def followups_due(session: Session, now: datetime, config: Config) -> list[Outreach]:
    cutoff = _utc(now) - timedelta(days=config.outreach.followup_days)
    followed = set(session.exec(select(Outreach.parent_id).where(Outreach.parent_id.is_not(None))).all())
    rows = session.exec(
        select(Outreach).where(Outreach.kind == "message", Outreach.status == "sent")
    ).all()
    return [
        r for r in rows if r.id not in followed and r.sent_at and _utc(r.sent_at) <= cutoff
    ]


def draft_followups(
    session: Session, config: Config, llm: LLMProvider, now: datetime
) -> list[Outreach]:
    if is_paused(session):
        return []
    atoms = AtomStore(session).confirmed_only()
    if not atoms:
        return []
    stored: list[Outreach] = []
    for parent in followups_due(session, now, config):
        job, contact = session.get(Job, parent.job_id), session.get(Contact, parent.contact_id)
        if job is None or contact is None:
            continue
        d = draft_followup(parent, job, contact, atoms, llm)
        if d is None:
            continue
        row = Outreach(
            job_id=job.id,
            contact_id=contact.id,
            kind="followup",
            text=d.text,
            atom_ids=list(d.atom_ids),
            parent_id=parent.id,
        )
        session.add(row)
        stored.append(row)
    _commit(session)
    return stored
