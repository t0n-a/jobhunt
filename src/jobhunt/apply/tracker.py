from collections import defaultdict
from datetime import UTC, datetime

from sqlmodel import Session, select

from jobhunt.tailor.models import Application, ApplicationEvent

STATUSES = (
    "drafted",
    "queued",
    "approved",
    "sent",
    "replied",
    "interview",
    "offer",
    "rejected",
    "ghosted",
    "skipped",
)
# In order: a stage counts as reached once any later stage is.
# Where the candidate can move an application next (Telegram verbs and the detail page buttons).
NEXT_STATUSES: dict[str, tuple[str, ...]] = {
    "replied": ("sent",),
    "interview": ("sent", "replied"),
    "offer": ("interview",),
    "rejected": ("sent", "replied", "interview"),
}
FUNNEL = ("sent", "replied", "interview", "offer")


def next_statuses(status: str) -> list[str]:
    return [target for target, sources in NEXT_STATUSES.items() if status in sources]


def set_status(
    session: Session,
    application: Application,
    status: str,
    *,
    note: str = "",
    now: datetime | None = None,
) -> Application:
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status!r}")
    now = now or datetime.now(UTC)
    if status == "sent" and application.applied_at is None:
        application.applied_at = now
    application.status = status
    session.add(application)
    session.add(ApplicationEvent(application_id=application.id, kind=status, note=note, at=now))
    _commit(session)
    return application


def add_event(
    session: Session,
    application: Application,
    kind: str,
    *,
    note: str = "",
    now: datetime | None = None,
) -> ApplicationEvent:
    event = ApplicationEvent(
        application_id=application.id, kind=kind, note=note, at=now or datetime.now(UTC)
    )
    session.add(event)
    _commit(session)
    return event


def events_for(session: Session, application_id: int) -> list[ApplicationEvent]:
    statement = (
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application_id)
        .order_by(ApplicationEvent.at, ApplicationEvent.id)
    )
    return list(session.exec(statement).all())


def funnel(session: Session) -> dict[str, int]:
    kinds: dict[int, set[str]] = defaultdict(set)
    for application_id, kind in session.exec(
        select(ApplicationEvent.application_id, ApplicationEvent.kind)
    ):
        kinds[application_id].add(kind)

    counts = dict.fromkeys(FUNNEL, 0)
    for application_id, status in session.exec(select(Application.id, Application.status)):
        seen = kinds[application_id] | {status}
        reached = max((FUNNEL.index(k) for k in seen if k in FUNNEL), default=-1)
        for stage in FUNNEL[: reached + 1]:
            counts[stage] += 1
    return counts


def _commit(session: Session) -> None:
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
