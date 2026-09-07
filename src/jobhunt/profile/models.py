from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class AtomKind(StrEnum):
    PROJECT = "project"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    COURSE = "course"
    SKILL = "skill"


class Provenance(StrEnum):
    INTERVIEW = "interview"
    CV = "cv"
    GITHUB = "github"
    COURSEWORK = "coursework"
    QUEST = "quest"
    MANUAL = "manual"


def _now() -> datetime:
    return datetime.now(UTC)


class EvidenceAtom(SQLModel, table=True):
    """One bullet-worthy fact. Tailoring selects and rephrases these; it never
    invents them."""

    __tablename__ = "evidence_atom"

    id: int | None = Field(default=None, primary_key=True)
    kind: AtomKind
    title: str
    description: str
    skills: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    metrics: str | None = None
    provenance: Provenance
    source_ref: str | None = None
    started_on: date | None = None
    ended_on: date | None = None
    confirmed: bool = False
    created_at: datetime = Field(default_factory=_now)
