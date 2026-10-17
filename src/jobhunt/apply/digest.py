from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from jobhunt.apply.followup import followups_due
from jobhunt.apply.queue import autopilot_available, trust_count
from jobhunt.apply.tracker import funnel
from jobhunt.config import Config
from jobhunt.jobs.models import Job
from jobhunt.ranking.shortlist import _clean
from jobhunt.state import is_paused
from jobhunt.tailor.models import Application, ApplicationEvent

# Keeps the Telegram message short; the web UI lists everything.
MAX_LINES = 10


def _label(session: Session, application_id: int) -> str:
    application = session.get(Application, application_id)
    job = session.get(Job, application.job_id) if application else None
    title, company = (_clean(job.title), _clean(job.company)) if job else ("?", "?")
    return f"#{application_id} {title} @ {company}"


def _section(header: str, lines: list[str]) -> list[str]:
    return [f"{header}: {len(lines)}", *(f"  {x}" for x in lines[:MAX_LINES])]


def build_digest(
    session: Session,
    *,
    now: datetime,
    scan_line: str,
    linkedin_skipped: bool,
    ghosted: list[Application],
    config: Config,
) -> str:
    out = [f"jobhunt daily, {now:%Y-%m-%d}"]
    if is_paused(session):
        out.append("Paused: nothing new is tailored or queued. Send resume to restart.")
    out.append(f"Scan: {scan_line.splitlines()[0] if scan_line else ''}")
    if linkedin_skipped:
        out.append("LinkedIn was skipped this run (blocked or slow).")

    since = now - timedelta(days=1)
    sent = {
        e.application_id
        for e in session.exec(select(ApplicationEvent).where(ApplicationEvent.kind == "sent"))
        if e.at.replace(tzinfo=UTC) >= since
    }
    out += _section("Sent in the last day", [_label(session, i) for i in sorted(sent)])

    queued = session.exec(select(Application).where(Application.status == "queued")).all()
    out += _section(
        "Waiting on you",
        [f"{_label(session, a.id)}: yes {a.id} / no {a.id}" for a in queued],
    )
    approved = session.exec(select(Application).where(Application.status == "approved")).all()
    out += _section(
        "Approved, apply when ready",
        [f"{_label(session, a.id)}: sent {a.id} once it's out" for a in approved],
    )
    due = followups_due(session, now)
    out += _section(
        "Follow-ups due",
        [f"{_label(session, f.application_id)}: {f.text}  (done {f.application_id})" for f in due],
    )
    if ghosted:
        out.append(f"Marked ghosted: {len(ghosted)}")

    f = funnel(session)
    out.append(
        f"Funnel: sent {f['sent']}, replied {f['replied']}, "
        f"interview {f['interview']}, offer {f['offer']}"
    )
    cfg = config.autopilot
    note = "; autopilot can now be switched on" if autopilot_available(session, cfg) else ""
    out.append(f"Trust: {trust_count(session)}/{cfg.trust_target} clean approvals{note}")
    return "\n".join(out)
