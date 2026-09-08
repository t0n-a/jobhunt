import hashlib
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


def posting_hash(title: str, company: str, description: str) -> str:
    """Stable content hash used for dedupe and for the score cache."""
    payload = "\x1f".join(part.strip().casefold() for part in (title, company, description))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RawPosting(SQLModel, table=True):
    """One posting exactly as a source returned it, before dedupe."""

    __tablename__ = "raw_posting"

    id: int | None = Field(default=None, primary_key=True)
    source: str
    external_id: str
    url: str
    title: str
    company: str
    location: str | None = None
    remote: bool = False
    description: str
    posted_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=_now)
    content_hash: str
    job_id: int | None = Field(default=None, foreign_key="job.id")


class Job(SQLModel, table=True):
    """A canonical posting after dedupe. One row per real-world opening."""

    __tablename__ = "job"

    id: int | None = Field(default=None, primary_key=True)
    title: str
    company: str
    location: str | None = None
    remote: bool = False
    description: str
    url: str
    first_seen: datetime = Field(default_factory=_now)
    last_seen: datetime = Field(default_factory=_now)
    content_hash: str
