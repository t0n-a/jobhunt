import copy
from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class Application(SQLModel, table=True):
    """One tailored application: the artefacts, and what the check found.

    The record exists from the moment the resume is composed, which is before
    any PDF has been built — so every render-time field is nullable or empty
    rather than required.
    """

    __tablename__ = "application"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id")
    created_at: datetime = Field(default_factory=_now)
    status: str = "drafted"
    applied_at: datetime | None = None
    # These JSON columns are NOT mutation-tracked. Any change must ASSIGN A NEW
    # OBJECT rather than mutate in place, or it silently fails to persist.
    keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    resume_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    cover_letter: str = ""
    # What the verifier refused, as {atom_id, text, reason}. Shown in the UI:
    # hiding rejects would make a verifier bug look like a well-behaved model.
    dropped_bullets: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    pdf_path: str | None = None
    coverage: float | None = None
    missing_keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))


def iter_bullets(resume_json: dict) -> list[tuple[int, dict]]:
    """Every bullet in document order, paired with its flat ordinal.

    The ordinal is the address the UI edits by: one atom can back several
    bullets, so an atom id does not identify a row.
    """
    out: list[tuple[int, dict]] = []
    index = 0
    for section in (resume_json or {}).get("sections", []):
        for entry in section.get("entries", []):
            for bullet in entry.get("bullets", []):
                out.append((index, bullet))
                index += 1
    return out


def replace_bullets(resume_json: dict, bullets: list[dict]) -> dict:
    """Return a NEW document with `bullets` distributed back in document order.

    Used after verification: the survivors are laid back into the structure they
    came from, and entries whose every bullet was dropped simply end up empty.
    """
    updated = copy.deepcopy(resume_json or {})
    remaining = list(bullets)
    for section in updated.get("sections", []):
        for entry in section.get("entries", []):
            kept = []
            for _ in entry.get("bullets", []):
                if remaining:
                    kept.append(remaining.pop(0))
            entry["bullets"] = kept
    return updated


def _bullet_at(updated: dict, index: int) -> dict:
    for position, bullet in iter_bullets(updated):
        if position == index:
            return bullet
    raise IndexError(f"No bullet at index {index}")


def edit_bullet(resume_json: dict, index: int, text: str) -> dict:
    """Return a NEW document with one bullet rewritten by the user.

    The bullet is marked `source="user"`, which exempts it from the verifier:
    these are the user's own words, and they are responsible for them.
    """
    updated = copy.deepcopy(resume_json or {})
    bullet = _bullet_at(updated, index)
    bullet["text"] = text
    bullet["source"] = "user"
    return updated


def delete_bullet(resume_json: dict, index: int) -> dict:
    """Return a NEW document with one bullet removed."""
    updated = copy.deepcopy(resume_json or {})
    target = _bullet_at(updated, index)
    for section in updated.get("sections", []):
        for entry in section.get("entries", []):
            entry["bullets"] = [b for b in entry.get("bullets", []) if b is not target]
    return updated
