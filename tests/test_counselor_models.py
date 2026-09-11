from sqlmodel import Session, select

from jobhunt.counselor.models import NOTE_KINDS, Conversation, CounselorNote, Turn
from jobhunt.db import create_engine_for, init_db


def test_tables_round_trip_a_turn_with_a_json_action(tmp_path):
    engine = create_engine_for(tmp_path / "db.sqlite")
    init_db(engine)
    with Session(engine) as db:
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)
        db.add(
            Turn(
                conversation_id=conv.id,
                role="counselor",
                text="Hi",
                action={"action": "ask", "text": "Hi", "quick_replies": ["a", "b"]},
            )
        )
        db.add(CounselorNote(conversation_id=conv.id, kind="goal", text="ML"))
        db.commit()

    with Session(engine) as db:
        turn = db.exec(select(Turn)).one()
        note = db.exec(select(CounselorNote)).one()
    assert turn.action["quick_replies"] == ["a", "b"]
    assert turn.proposal_state is None and turn.atom_id is None
    assert note.status == "open" and note.kind in NOTE_KINDS
