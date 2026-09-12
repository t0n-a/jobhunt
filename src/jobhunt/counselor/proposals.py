"""Turning an accepted proposal into an unconfirmed atom. The user pressed
Accept; the atom still has to be confirmed on /atoms like any model-written one."""

from sqlmodel import Session

from jobhunt.counselor.actions import ProposeAtom, parse_action
from jobhunt.counselor.models import Turn
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore


def _open_proposal(session: Session, turn_id: int) -> tuple[Turn, ProposeAtom]:
    turn = session.get(Turn, turn_id)
    if turn is None:
        raise KeyError(f"No counselor turn with id {turn_id}")
    if turn.proposal_state != "open" or turn.action is None:
        raise ValueError("That turn is not an open proposal")
    action = parse_action(turn.action)
    visible = action.then if hasattr(action, "then") else action
    if not isinstance(visible, ProposeAtom):
        raise ValueError("That turn is not a proposal")  # noqa: TRY004
    return turn, visible


def accept(
    session: Session,
    turn_id: int,
    *,
    title: str | None = None,
    description: str | None = None,
    skills: list[str] | None = None,
    metrics: str | None = None,
) -> EvidenceAtom:
    turn, proposal = _open_proposal(session, turn_id)
    draft = proposal.atom
    atom = AtomStore(session).add(
        EvidenceAtom(
            kind=AtomKind(draft.kind),
            title=(title or draft.title).strip(),
            description=(description or draft.description).strip(),
            skills=[
                s.strip()
                for s in (skills if skills is not None else list(draft.skills))
                if s.strip()
            ],
            metrics=(metrics if metrics is not None else draft.metrics) or None,
            provenance=Provenance.INTERVIEW,
            source_ref=f"counselor:{turn.id}",
            confirmed=False,
        )
    )
    turn.proposal_state = "accepted"
    turn.atom_id = atom.id
    session.add(turn)
    session.commit()
    session.refresh(atom)
    return atom


def reject(session: Session, turn_id: int) -> Turn:
    turn, _ = _open_proposal(session, turn_id)
    turn.proposal_state = "rejected"
    session.add(turn)
    session.commit()
    session.refresh(turn)
    return turn
