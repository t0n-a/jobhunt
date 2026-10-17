"""Who gets an outreach draft, the stored drafts, their status, and which follow-ups are due."""

import logging
import os
from datetime import UTC, datetime, timedelta

import httpx
from sqlmodel import Session, select

from jobhunt.config import Config
from jobhunt.jobs.dedupe import normalize
from jobhunt.jobs.models import Job
from jobhunt.llm import LLMProvider
from jobhunt.outreach.contacts import contact_for
from jobhunt.outreach.draft import draft_followup, draft_outreach
from jobhunt.outreach.models import Contact, Outreach
from jobhunt.outreach.search import search_recruiters
from jobhunt.profile.store import AtomStore
from jobhunt.ranking.shortlist import shortlist
from jobhunt.state import get_state, is_paused, set_state
from jobhunt.tailor.models import Application

log = logging.getLogger(__name__)

# An application in any of these is live; a rejected or withdrawn one is not worth a recruiter's time.
LIVE_STATUSES = ("queued", "approved", "sent")
OPEN = ("drafted", "sent")
# An unanswered draft must not block its company forever.
STALE_DRAFT_DAYS = 14
# A job whose draft keeps failing would take a per_run slot every day and starve the rest.
MAX_DRAFT_ATTEMPTS = 2
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
    cutoff = datetime.now(UTC) - timedelta(days=STALE_DRAFT_DAYS)
    rows = session.exec(
        select(Job.company, Outreach.status, Outreach.created_at)
        .join(Outreach, Outreach.job_id == Job.id)
        .where(Outreach.status.in_(OPEN))
    ).all()
    return {normalize(c) for c, status, made in rows if status == "sent" or _utc(made) > cutoff}


def _skip_stale(session: Session, company: str) -> None:
    """A stale draft no longer blocks its company, so its Sent/Skip buttons must stop working."""
    cutoff = datetime.now(UTC) - timedelta(days=STALE_DRAFT_DAYS)
    rows = session.exec(
        select(Outreach, Job.company)
        .join(Job, Outreach.job_id == Job.id)
        .where(Outreach.status == "drafted")
    ).all()
    for row, c in rows:
        if normalize(c) == company and _utc(row.created_at) <= cutoff:
            row.status = "skipped"
            session.add(row)


def _fail_key(job: Job) -> str:
    return f"outreach_fail:{job.id}"


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


_HTTP = httpx.Client()


def _search():
    key = os.environ.get("TAVILY_API_KEY")
    return (lambda company: search_recruiters(company, api_key=key, http=_HTTP)) if key else None


def _eligible(session: Session, job: Job, config: Config) -> Contact | None:
    if normalize(job.company) in _open_companies(session):
        return None
    contact = contact_for(session, job, search=_search(), monthly_cap=config.outreach.search_per_month)
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
        if int(get_state(session, _fail_key(job), "0") or 0) >= MAX_DRAFT_ATTEMPTS:
            continue
        if company in companies or _eligible(session, job, config) is None:
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
        contact = _eligible(session, job, config)  # re-check: earlier drafts this run may have used it
        if contact is None:
            continue
        result = draft_outreach(job, contact, atoms, llm, profile=config.profile)
        # A lone note would mark the contact contacted and never get its message.
        if not any(d.kind == "message" for d in result.drafts):
            log.warning("outreach: no usable draft for job %s (%s); retry next run", job.id, result.dropped)
            if not any(d.get("reason") == "llm error" for d in result.dropped):  # an outage is not the job's fault
                n = int(get_state(session, _fail_key(job), "0") or 0)
                set_state(session, _fail_key(job), str(n + 1))
            continue
        _skip_stale(session, normalize(job.company))
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
