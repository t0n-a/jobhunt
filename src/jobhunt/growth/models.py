from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, select


def _now() -> datetime:
    return datetime.now(UTC)


class Suggestion(SQLModel, table=True):
    """One change to the candidate's GitHub profile, waiting for his yes/no."""

    __tablename__ = "growth_suggestion"

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # publish | readme | profile_readme | pin | cert
    target: str  # repo full name, or a cert key
    title: str
    body: str = ""  # README text, or the explanation
    flags: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=True))
    blocking: bool = False
    # suggested | approved | done | declined | failed
    status: str = "suggested"
    error: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


def upsert_suggestion(session: Session, kind: str, target: str, **fields) -> Suggestion:
    """One row per (kind, target). A declined or done row is never reopened."""
    row = session.exec(
        select(Suggestion).where(Suggestion.kind == kind, Suggestion.target == target)
    ).first()
    if row is None:
        row = Suggestion(kind=kind, target=target, **fields)
    elif row.status in ("declined", "done"):
        return row
    else:
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = _now()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
