"""Goal and constraint notes, rendered for the scorer and the LinkedIn composer.

Only these two kinds leave the counselor: a preference is not a scoring
signal and a thread is the counselor's own business. Closed notes never
render — closing one is how the user withdraws it.
"""

from sqlmodel import Session, select

from jobhunt.counselor.models import CounselorNote

SCORING_KINDS = ("goal", "constraint")


def open_notes(session: Session, kinds: tuple[str, ...]) -> list[CounselorNote]:
    return list(
        session.exec(
            select(CounselorNote)
            .where(CounselorNote.status == "open", CounselorNote.kind.in_(kinds))  # type: ignore[attr-defined]
            .order_by(CounselorNote.id)
        ).all()
    )


def _line(label: str, notes: list[CounselorNote], kind: str) -> str:
    texts = [
        n.text.strip().rstrip(".")
        for n in notes
        if n.kind == kind and n.status == "open" and n.text.strip()
    ]
    return f"{label}: {'; '.join(texts)}." if texts else ""


def render_for_scoring(notes: list[CounselorNote]) -> str:
    lines = [
        _line("Candidate goals", notes, "goal"),
        _line("Candidate constraints", notes, "constraint"),
    ]
    return "\n".join(line for line in lines if line)


def render_goals(notes: list[CounselorNote]) -> str:
    return _line("Candidate goals", notes, "goal")


def scoring_block(session: Session) -> str:
    return render_for_scoring(open_notes(session, SCORING_KINDS))


def goals_block(session: Session) -> str:
    return render_goals(open_notes(session, ("goal",)))
