from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class Contact(SQLModel, table=True):
    """A person to write to about a job, named only by the posting or the fallback search."""

    __tablename__ = "contact"

    id: int | None = Field(default=None, primary_key=True)
    company: str
    name: str | None = None
    role: str | None = None
    profile_url: str | None = None
    email: str | None = None
    # "posting" when the posting names them; "fallback" for a LinkedIn search URL.
    source: str
    created_at: datetime = Field(default_factory=_now)


class Outreach(SQLModel, table=True):
    """One drafted message, connection note or follow-up. The candidate sends it himself."""

    __tablename__ = "outreach"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id")
    contact_id: int | None = Field(default=None, foreign_key="contact.id")
    kind: str  # message | connection_note | followup
    text: str
    # Evidence atoms the draft cites; a None column reads as no citations.
    atom_ids: list[int] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    status: str = "drafted"  # drafted | sent | replied | skipped
    # A follow-up points at the message it follows up.
    parent_id: int | None = Field(default=None, foreign_key="outreach.id")
    created_at: datetime = Field(default_factory=_now)
    sent_at: datetime | None = None
    replied_at: datetime | None = None
