"""What the system already knows, rendered for the model without an LLM call.

The briefing is the counselor's grounding: it never asks about what is here,
it probes what is thin, and it picks up the threads it left itself.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, func, select

from jobhunt.config import Config
from jobhunt.counselor.models import NOTE_KINDS, Conversation, CounselorNote
from jobhunt.counselor.tools import atom_line, cached_repos
from jobhunt.growth.gaps import compute_gaps
from jobhunt.jobs.models import Job, Score
from jobhunt.profile.models import EvidenceAtom

GAP_LIMIT = 5
REPO_LIMIT = 10


@dataclass(frozen=True)
class Briefing:
    first_conversation: bool
    days_since: int | None
    confirmed: int
    unconfirmed: int
    atoms: tuple[str, ...]
    thin: tuple[str, ...]
    jobs_total: int
    jobs_scored: int
    gaps: tuple[str, ...]
    repos_uncovered: tuple[str, ...]
    repos_note: str
    notes: dict[str, tuple[str, ...]]


def _is_thin(atom: EvidenceAtom) -> bool:
    return not atom.metrics and len(atom.skills or []) < 2


def _days_since(conversation: Conversation | None) -> int | None:
    if conversation is None or conversation.last_turn_at is None:
        return None
    last = conversation.last_turn_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return max(0, (datetime.now(UTC) - last).days)


def build_briefing(
    session: Session, *, config: Config, conversation: Conversation | None
) -> Briefing:
    atoms = list(session.exec(select(EvidenceAtom).order_by(EvidenceAtom.id)).all())
    jobs_total = session.exec(select(func.count()).select_from(Job)).one()
    jobs_scored = session.exec(select(func.count(func.distinct(Score.job_id)))).one()

    gaps = tuple(
        f"{g.skill} — missing from {g.job_count} {'job' if g.job_count == 1 else 'jobs'}"
        + (f" (e.g. {g.example_titles[0]})" if g.example_titles else "")
        for g in compute_gaps(session, limit=GAP_LIMIT)
    )

    repos, repos_note = cached_repos(
        config.profile.github_username, os.environ.get("GITHUB_TOKEN"), config.llm.cache_dir
    )
    refs = " ".join(a.source_ref or "" for a in atoms)
    uncovered = tuple(
        f"{r.get('full_name') or r.get('name')} ({r.get('language') or 'unknown'})"
        for r in repos
        if r.get("name") and r["name"] not in refs and (r.get("full_name") or "") not in refs
    )[:REPO_LIMIT]

    notes: dict[str, tuple[str, ...]] = {}
    if conversation is not None:
        open_notes = session.exec(
            select(CounselorNote)
            .where(CounselorNote.conversation_id == conversation.id, CounselorNote.status == "open")
            .order_by(CounselorNote.id)
        ).all()
        for kind in NOTE_KINDS:
            texts = tuple(n.text for n in open_notes if n.kind == kind)
            if texts:
                notes[kind] = texts

    counselor_has_spoken = conversation is not None and conversation.last_turn_at is not None
    return Briefing(
        first_conversation=not counselor_has_spoken,
        days_since=_days_since(conversation),
        confirmed=sum(1 for a in atoms if a.confirmed),
        unconfirmed=sum(1 for a in atoms if not a.confirmed),
        atoms=tuple(atom_line(a) for a in atoms),
        thin=tuple(a.title for a in atoms if _is_thin(a)),
        jobs_total=jobs_total,
        jobs_scored=jobs_scored,
        gaps=gaps,
        repos_uncovered=uncovered,
        repos_note=repos_note,
        notes=notes,
    )


_NOTE_HEADINGS = {
    "goal": "## Goals they have stated",
    "constraint": "## Constraints they have stated",
    "preference": "## Preferences",
    "thread": "## Open threads (your own to-do list — close one with a note action once handled)",
    "observation": "## Observations about how to talk to them",
}


def render_briefing(b: Briefing) -> str:
    lines: list[str] = ["# Briefing"]
    if b.first_conversation:
        lines.append(
            "This is the first conversation. Introduce yourself in two sentences and ask one easy, concrete question."
        )
    elif b.days_since is not None and b.days_since >= 3:
        lines.append(
            f"It has been {b.days_since} days since you last talked. Say so lightly and pick up an open thread."
        )
    else:
        lines.append(f"Last talked {b.days_since} day(s) ago.")

    lines.append(f"\n## Evidence atoms ({b.confirmed} confirmed, {b.unconfirmed} unconfirmed)")
    lines.extend(b.atoms or ["No evidence atoms yet."])
    if b.thin:
        lines.append("\n## Thin atoms (no numbers, fewer than two skills — probe these)")
        lines.extend(f"- {t}" for t in b.thin)

    lines.append(f"\n## Jobs: {b.jobs_scored} scored of {b.jobs_total}")
    if b.gaps:
        lines.append("Top gaps (skills their best-fit jobs want that no atom shows):")
        lines.extend(f"- {g}" for g in b.gaps)

    lines.append("\n## GitHub repos not covered by any atom")
    if b.repos_note:
        lines.append(f"(unavailable: {b.repos_note})")
    lines.extend(f"- {r}" for r in b.repos_uncovered)
    if not b.repos_note and not b.repos_uncovered:
        lines.append("(none)")

    for kind, heading in _NOTE_HEADINGS.items():
        if kind in b.notes:
            lines.append(f"\n{heading}")
            lines.extend(f"- {t}" for t in b.notes[kind])
    return "\n".join(lines)
