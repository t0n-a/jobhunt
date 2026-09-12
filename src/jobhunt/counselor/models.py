"""The counselor's tables: one conversation, its turns, and its notes."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

NOTE_KINDS = ("goal", "constraint", "preference", "thread", "observation")
ROLES = ("user", "counselor")
PROPOSAL_STATES = ("open", "accepted", "rejected")


def _now() -> datetime:
    return datetime.now(UTC)


class Conversation(SQLModel, table=True):
    """There is one of these. A second row would be a deliberate 'start over'."""

    __tablename__ = "counselor_conversation"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    last_turn_at: datetime | None = None


class Turn(SQLModel, table=True):
    """One message in the chat. Counselor turns keep the whole parsed action."""

    __tablename__ = "counselor_turn"

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="counselor_conversation.id")
    role: str
    text: str
    # Not mutation-tracked: assign a new dict, never mutate in place.
    action: dict | None = Field(default=None, sa_column=Column(JSON))
    proposal_state: str | None = None
    atom_id: int | None = Field(default=None, foreign_key="evidence_atom.id")
    created_at: datetime = Field(default_factory=_now)


class CounselorNote(SQLModel, table=True):
    """What the counselor has learned that is not evidence: goals, constraints,
    preferences, its own open threads, and observations about how to ask."""

    __tablename__ = "counselor_note"

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="counselor_conversation.id")
    kind: str
    text: str
    status: str = "open"
    source_turn_id: int | None = Field(default=None, foreign_key="counselor_turn.id")
    created_at: datetime = Field(default_factory=_now)
