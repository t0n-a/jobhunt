from sqlmodel import Field, Session, SQLModel


class AppState(SQLModel, table=True):
    """App-wide settings and flags, one row per key."""

    __tablename__ = "app_state"

    key: str = Field(primary_key=True)
    value: str


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
