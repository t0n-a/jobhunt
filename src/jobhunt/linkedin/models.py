"""The LinkedIn buildout tables.

State lives in ROWS rather than in a JSON blob on one row, deliberately.
Approval is per pasteable unit, which means writing to one unit at a time, and
a write into a nested JSON column is not mutation-tracked: it commits without
error and persists nothing. Rows make that trap unreachable here.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class LinkedinProfile(SQLModel, table=True):
    """One generated version. Never edited in place; a rebuild adds a new one."""

    __tablename__ = "linkedin_profile"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    # These JSON columns are NOT mutation-tracked. Any change must ASSIGN A NEW
    # OBJECT rather than mutate in place, or it silently fails to persist.
    keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # Which derivation produced those keywords: "applications" or "atoms". The
    # page states it, because a profile aimed at nothing is a different artefact
    # from one aimed at twelve applications.
    keyword_source: str = "atoms"
    # The shortlist this version was allowed to draw on. Stored rather than
    # recomputed: the atom store changes, and without this a rejection cannot be
    # explained a week later against a shortlist that no longer exists.
    atom_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    # What the verifier refused, as {field, text, reason}. Shown, never hidden:
    # hiding rejects makes a verifier bug look like a well-behaved model.
    dropped: list[dict] = Field(default_factory=list, sa_column=Column(JSON))


class LinkedinField(SQLModel, table=True):
    """One pasteable unit of one version.

    Headline, about and skills are one row each. Experience and projects get one
    row per entry, because the user pastes an entry at a time into LinkedIn and
    therefore approves an entry at a time.
    """

    __tablename__ = "linkedin_field"

    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="linkedin_profile.id")
    name: str
    # Empty for the single-row fields; the entry's heading for experience and
    # projects, so the user knows which job they are looking at.
    entry_title: str = ""
    # Set for entry rows, None for prose and skills, which synthesise many atoms.
    atom_id: int | None = None
    # Stable display order within one name. Insertion order is not order.
    position: int = 0
    text: str = ""
    source: str = "model"
    over_limit: bool = False
    # Null until a human explicitly approves THIS unit. See LinkedinStore.
    approved_at: datetime | None = None
    # "user" today. Reserved for "auto" if some field kinds are later allowed to
    # publish unattended — the record is what makes that decision reviewable.
    approved_by: str | None = None
