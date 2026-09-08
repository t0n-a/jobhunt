from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class Energy(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"

    @property
    def minutes(self) -> int:
        return {"low": 15, "normal": 45, "high": 120}[self.value]


class VerificationType(StrEnum):
    GITHUB_COMMIT = "github_commit"
    GITHUB_REPO_EXISTS = "github_repo_exists"
    GITHUB_FILE_CONTAINS = "github_file_contains"
    MANUAL = "manual"


class Quest(SQLModel, table=True):
    """A multi-day project that closes one skill gap and earns one resume line.

    Exactly one is active at a time: the whole design is one visible thing.
    """

    __tablename__ = "quest"

    id: int | None = Field(default=None, primary_key=True)
    skill: str
    # The motivating number, carried from the gap so the task can say WHY it
    # matters. With ADHD an unexplained task reads as arbitrary and gets skipped.
    gap_job_count: int = 0
    title: str
    deliverable: str
    target_bullet: str
    repo_url: str | None = None
    status: str = "active"
    created_at: datetime = Field(default_factory=_now)


class QuestStep(SQLModel, table=True):
    """One day-sized slice of a quest, with a machine-checkable done condition."""

    __tablename__ = "quest_step"

    id: int | None = Field(default=None, primary_key=True)
    quest_id: int = Field(foreign_key="quest.id")
    ordinal: int
    title: str
    instructions: str
    # Not mutation-tracked: assign a new dict, never mutate in place.
    verification: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "pending"
    completed_at: datetime | None = None
    proof_note: str | None = None
    atom_id: int | None = Field(default=None, foreign_key="evidence_atom.id")
