import pytest
from sqlmodel import Session, select

from jobhunt.counselor.models import Conversation, Turn
from jobhunt.counselor.proposals import accept, reject
from jobhunt.db import create_engine_for, init_db
from jobhunt.profile.models import EvidenceAtom, Provenance


@pytest.fixture
def db(tmp_path):
    engine = create_engine_for(tmp_path / "db.sqlite")
    init_db(engine)
    with Session(engine) as session:
        conv = Conversation()
        session.add(conv)
        session.commit()
        session.refresh(conv)
        session.add(
            Turn(
                conversation_id=conv.id,
                role="counselor",
                text="I'd record this:",
                proposal_state="open",
                action={
                    "action": "propose_atom",
                    "text": "I'd record this:",
                    "atom": {
                        "kind": "project",
                        "title": "Shortener",
                        "description": "Flask and Redis.",
                        "skills": ["Python", "Redis"],
                        "metrics": None,
                    },
                    "because": "you said so",
                },
            )
        )
        session.add(
            Turn(
                conversation_id=conv.id,
                role="counselor",
                text="plain ask",
                action={"action": "ask", "text": "plain ask"},
            )
        )
        session.commit()
        yield session


def test_accept_writes_an_unconfirmed_interview_atom_linked_to_the_turn(db):
    atom = accept(db, 1)
    stored = db.exec(select(EvidenceAtom)).one()
    assert stored.id == atom.id
    assert (stored.title, stored.skills, stored.confirmed) == (
        "Shortener",
        ["Python", "Redis"],
        False,
    )
    assert stored.provenance == Provenance.INTERVIEW and stored.source_ref == "counselor:1"
    turn = db.get(Turn, 1)
    assert turn.proposal_state == "accepted" and turn.atom_id == atom.id


def test_accept_applies_the_users_edits(db):
    atom = accept(db, 1, title="URL shortener", skills=["Python"], metrics="2k req/s")
    assert (atom.title, atom.skills, atom.metrics, atom.description) == (
        "URL shortener",
        ["Python"],
        "2k req/s",
        "Flask and Redis.",
    )


def test_accept_twice_and_accept_a_non_proposal_are_errors(db):
    accept(db, 1)
    with pytest.raises(ValueError):
        accept(db, 1)
    with pytest.raises(ValueError):
        accept(db, 2)
    with pytest.raises(KeyError):
        accept(db, 99)


def test_reject_marks_the_turn_and_writes_nothing(db):
    turn = reject(db, 1)
    assert turn.proposal_state == "rejected" and turn.atom_id is None
    assert db.exec(select(EvidenceAtom)).all() == []
    with pytest.raises(ValueError):
        reject(db, 1)
