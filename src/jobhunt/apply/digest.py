from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from jobhunt.apply.autopilot import autopilot_on, sent_today
from jobhunt.apply.followup import followups_due
from jobhunt.apply.queue import autopilot_available, trust_count
from jobhunt.apply.tracker import funnel
from jobhunt.config import Config
from jobhunt.jobs.models import Job
from jobhunt.outreach.log import followups_due as outreach_followups_due
from jobhunt.outreach.models import Outreach
from jobhunt.ranking.shortlist import _clean
from jobhunt.state import blocked_hosts, is_paused
from jobhunt.tailor.models import Application, ApplicationEvent

# Keeps the Telegram message short; the web UI lists everything.
MAX_LINES = 10


def _label(session: Session, application_id: int) -> str:
    application = session.get(Application, application_id)
    job = session.get(Job, application.job_id) if application else None
    title, company = (_clean(job.title), _clean(job.company)) if job else ("?", "?")
    return f"#{application_id} {title} @ {company}"


def _via(session: Session, application_id: int) -> str:
    application = session.get(Application, application_id)
    return " (autopilot)" if application and application.channel == "autopilot" else ""


def _company(session: Session, outreach: Outreach) -> str:
    job = session.get(Job, outreach.job_id)
    return _clean(job.company) if job else "?"


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
    out += _section("Sent in the last day", [_label(session, i) + _via(session, i) for i in sorted(sent)])

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
    rows = session.exec(select(Outreach)).all()
    drafted = [o for o in rows if o.status == "drafted"]
    replied = sum(o.status == "replied" for o in rows)
    # People written to, not rows: a message, note and follow-up to one person count once.
    sent_total = len(
        {
            o.contact_id if o.contact_id is not None else f"row{o.id}"
            for o in rows
            if o.status in ("sent", "replied") and o.kind in ("message", "connection_note")
        }
    )
    if rows:
        out.append(f"Outreach: {len(drafted)} to send, {sent_total} sent, {replied} replied")
        for o in drafted[:5]:
            out.append(f"  #{o.id} {_company(session, o)}: osent {o.id} / oskip {o.id}")
        for o in outreach_followups_due(session, now, config):
            out.append(f"  Follow-up due: #{o.id} {_company(session, o)}")
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
    if autopilot_on(session):
        out += [f"Autopilot blocked: {b.host} ({_clean(b.reason)})" for b in blocked_hosts(session)[:5]]
    out.append(
        f"Autopilot: {'on' if autopilot_on(session) else 'off'}, "
        f"{sent_today(session, now)}/{cfg.daily_cap} today"
    )
    return "\n".join(out)
