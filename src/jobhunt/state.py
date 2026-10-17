from datetime import UTC, datetime

from sqlmodel import Field, Session, SQLModel


class AppState(SQLModel, table=True):
    """App-wide settings and flags, one row per key."""

    __tablename__ = "app_state"

    key: str = Field(primary_key=True)
    value: str


class BlockedHost(SQLModel, table=True):
    """A host autopilot must never submit to again (a failed submit may have gone through)."""

    __tablename__ = "blocked_host"

    host: str = Field(primary_key=True)
    reason: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def block_host(session: Session, host: str, reason: str) -> None:
    session.merge(BlockedHost(host=host, reason=reason))
    _commit(session)


def is_blocked(session: Session, host: str) -> bool:
    return session.get(BlockedHost, host) is not None


def get_state(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.get(AppState, key)
    return row.value if row else default


def set_state(session: Session, key: str, value: str) -> None:
    session.merge(AppState(key=key, value=value))
    _commit(session)


def is_paused(session: Session) -> bool:
    return get_state(session, "paused", "0") == "1"


def set_paused(session: Session, paused: bool) -> None:
    set_state(session, "paused", "1" if paused else "0")


def _commit(session: Session) -> None:
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
