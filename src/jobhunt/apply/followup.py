from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from jobhunt.apply.tracker import add_event, events_for, set_status
from jobhunt.tailor.models import Application, ApplicationEvent

# career-ops cadence (see NOTICE): nudges at 7 d, then 7 d after the last, max 2, then cold.
NUDGE_GAP = timedelta(days=7)
MAX_NUDGES = 2
# replied: 1 d, then 3 d after the last follow-up; interview: one thank-you at 1 d.
REPLY_FIRST = timedelta(days=1)
REPLY_GAP = timedelta(days=3)
THANKS_AT = timedelta(days=1)
# 7 + 7 + 7: two nudge windows, then cold, even if the nudges were ignored.
COLD_AFTER = timedelta(days=21)
COLD_NOTE = "no reply after 2 follow-ups"


@dataclass
class FollowUp:
    application_id: int
    kind: str
    due_at: datetime
    text: str


def followups_due(session: Session, now: datetime) -> list[FollowUp]:
    statuses = ("sent", "replied", "interview")
    applications = session.exec(
        select(Application).where(Application.status.in_(statuses))
    ).all()
    found = [f for a in applications if (f := _next(session, a)) and f.due_at <= now]
    return sorted(found, key=lambda f: f.due_at)


def mark_followup_done(
    session: Session, application: Application, now: datetime | None = None
) -> None:
    add_event(session, application, "followup", now=now)


def mark_cold(session: Session, now: datetime) -> list[Application]:
    cold = []
    for application in session.exec(
        select(Application).where(Application.status == "sent")
    ).all():
        events = _stage_events(session, application)
        if events is None:
            continue
        stage, followups = events
        nudged_out = len(followups) >= MAX_NUDGES and _utc(followups[-1].at) + NUDGE_GAP <= now
        if nudged_out or _utc(stage.at) + COLD_AFTER <= now:
            set_status(session, application, "ghosted", note=COLD_NOTE, now=now)
            cold.append(application)
    return cold


def _next(session: Session, application: Application) -> FollowUp | None:
    events = _stage_events(session, application)
    if events is None:
        return None
    stage, followups = events
    done = len(followups)
    stage_at = _utc(stage.at)
    last = _utc(followups[-1].at) if followups else None
    app_id = application.id

    if application.status == "sent":
        if done >= MAX_NUDGES:
            return None
        due = (last or stage_at) + NUDGE_GAP
        text = f"Follow up on your application (nudge {done + 1}/{MAX_NUDGES})."
        return FollowUp(app_id, "nudge", due, text)
    if application.status == "replied":
        if done >= 2:
            return None
        due = stage_at + REPLY_FIRST if done == 0 else last + REPLY_GAP
        return FollowUp(app_id, "reply", due, "Reply to keep the conversation going.")
    if done:
        return None
    return FollowUp(
        app_id, "thanks", stage_at + THANKS_AT, "Send a thank-you note after your interview."
    )


def _stage_events(
    session: Session, application: Application
) -> tuple[ApplicationEvent, list[ApplicationEvent]] | None:
    """The latest event for the current status, and the follow-ups logged after it."""
    events = events_for(session, application.id)
    indexes = [i for i, e in enumerate(events) if e.kind == application.status]
    if not indexes:
        return None
    stage_index = indexes[-1]
    followups = [e for e in events[stage_index + 1 :] if e.kind == "followup"]
    return events[stage_index], followups


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
