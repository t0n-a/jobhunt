import hashlib
from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


# Descriptions are interpolated into an LLM prompt that AgyProvider passes as an
# argv element, so an unbounded one can exceed ARG_MAX and fail the whole call.
# Truncating at the model boundary keeps the hash, the store and the prompt
# agreeing on one bounded value.
MAX_DESCRIPTION_CHARS = 20_000


def truncate_description(text: str) -> str:
    if len(text) <= MAX_DESCRIPTION_CHARS:
        return text
    return text[:MAX_DESCRIPTION_CHARS] + "\n[truncated]"


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


class Score(SQLModel, table=True):
    """One LLM judgement of one job against the confirmed profile."""

    __tablename__ = "score"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id")
    fit_score: int
    # These JSON columns are NOT mutation-tracked. Any change must ASSIGN A NEW
    # LIST rather than mutate in place, or it silently fails to persist.
    matched_skills: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    missing_skills: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    red_flags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    seniority_fit: str
    track: str
    rationale: str
    model: str = ""
    content_hash: str
    scored_at: datetime = Field(default_factory=_now)
