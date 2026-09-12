import pytest
from sqlmodel import Session, select

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.counselor import tools
from jobhunt.counselor.engine import APOLOGY, get_or_create_conversation, respond
from jobhunt.counselor.models import CounselorNote, Turn
from jobhunt.db import create_engine_for, init_db
from jobhunt.llm import FakeProvider, LLMError
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance


@pytest.fixture
def config(tmp_path):
    return Config(
        profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"),
        llm=LLMConfig(cache_dir=tmp_path / "cache"),
        data_dir=tmp_path,
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "fetch_repos", lambda u, token=None: [])
    engine = create_engine_for(tmp_path / "db.sqlite")
    init_db(engine)
    with Session(engine) as session:
        yield session


ASK = {"action": "ask", "text": "What did you build last?"}


def test_opener_on_first_visit_stores_only_a_counselor_turn(db, config):
    llm = FakeProvider([ASK])
    reply = respond(db, llm, None, config=config)
    assert reply.text == "What did you build last?" and reply.user_turn is None
    turns = db.exec(select(Turn)).all()
    assert [t.role for t in turns] == ["counselor"]
    assert "first conversation" in llm.prompts[0]
    assert get_or_create_conversation(db).last_turn_at is not None


def test_user_message_then_ask(db, config):
    respond(db, FakeProvider([ASK]), None, config=config)
    reply = respond(
        db,
        FakeProvider(
            [{"action": "ask", "text": "How many users?", "quick_replies": ["<10", "10-100"]}]
        ),
        "A URL shortener.",
        config=config,
    )
    assert reply.user_turn.text == "A URL shortener."
    assert reply.turn.action["quick_replies"] == ["<10", "10-100"]
    assert [t.role for t in db.exec(select(Turn).order_by(Turn.id)).all()] == [
        "counselor",
        "user",
        "counselor",
    ]


def test_look_ups_run_and_feed_the_next_prompt(db, config):
    db.add(
        EvidenceAtom(
            kind=AtomKind.PROJECT,
            title="Shortener",
            description="Flask",
            skills=["Python"],
            provenance=Provenance.CV,
            confirmed=True,
        )
    )
    db.commit()
    llm = FakeProvider([{"action": "look_up", "tool": "atom", "args": {"id": 1}}, ASK])
    respond(db, llm, "hi", config=config)
    assert len(llm.prompts) == 2
    assert "look_up atom {'id': 1} -> [1] [project] Shortener" in llm.prompts[1]


def test_third_look_up_gets_the_budget_error(db, config):
    lk = {"action": "look_up", "tool": "gap", "args": {"skill": "Docker"}}
    llm = FakeProvider([lk, lk, lk, ASK])
    respond(db, llm, "hi", config=config)
    assert len(llm.prompts) == 4
    assert "look-up budget spent" in llm.prompts[3]


def test_note_then_ask_writes_the_note_and_shows_the_ask(db, config):
    reply = respond(
        db,
        FakeProvider([{"action": "note", "kind": "goal", "text": "wants ML", "then": ASK}]),
        "I want to move into ML.",
        config=config,
    )
    note = db.exec(select(CounselorNote)).one()
    assert (note.kind, note.text, note.status) == ("goal", "wants ML", "open")
    assert note.source_turn_id == reply.user_turn.id
    assert reply.text == ASK["text"] and reply.turn.action["action"] == "note"


def test_closing_a_thread_marks_it_closed(db, config):
    conv = get_or_create_conversation(db)
    db.add(CounselorNote(conversation_id=conv.id, kind="thread", text="ask about the internship"))
    db.commit()
    respond(
        db,
        FakeProvider(
            [
                {
                    "action": "note",
                    "kind": "thread",
                    "text": "ask about the internship",
                    "close": True,
                    "then": ASK,
                }
            ]
        ),
        "The internship was at Acme.",
        config=config,
    )
    assert db.exec(select(CounselorNote)).one().status == "closed"


def test_end_sitting_records_next_time_as_a_thread(db, config):
    reply = respond(
        db,
        FakeProvider(
            [
                {
                    "action": "end_sitting",
                    "summary": "Good stop.",
                    "next_time": "ask about the thesis",
                }
            ]
        ),
        "I'm tired.",
        config=config,
    )
    assert reply.text == "Good stop." and reply.turn.action["action"] == "end_sitting"
    thread = db.exec(select(CounselorNote)).one()
    assert (thread.kind, thread.text) == ("thread", "ask about the thesis")


def test_propose_atom_is_stored_open_and_writes_no_atom(db, config):
    reply = respond(
        db,
        FakeProvider(
            [
                {
                    "action": "propose_atom",
                    "text": "I'd record this:",
                    "atom": {
                        "kind": "project",
                        "title": "Shortener",
                        "description": "Flask",
                        "skills": ["Python"],
                    },
                    "because": "you said 'a URL shortener in Flask'",
                }
            ]
        ),
        "a URL shortener in Flask",
        config=config,
    )
    assert reply.turn.proposal_state == "open" and reply.turn.atom_id is None
    assert db.exec(select(EvidenceAtom)).all() == []


def test_garbage_twice_is_an_apology_with_no_counselor_turn(db, config):
    reply = respond(db, FakeProvider(["nonsense", {"action": "dance"}]), "hi", config=config)
    assert reply.turn is None and reply.text == APOLOGY
    assert [t.role for t in db.exec(select(Turn)).all()] == ["user"]


def test_garbage_once_then_valid_is_fine(db, config):
    reply = respond(db, FakeProvider(["nonsense", ASK]), "hi", config=config)
    assert reply.turn is not None and reply.text == ASK["text"]


def test_provider_failure_propagates(db, config):
    with pytest.raises(LLMError):
        respond(db, FakeProvider([]), "hi", config=config)


def test_over_length_message_is_refused_before_any_call(db, config):
    llm = FakeProvider([ASK])
    with pytest.raises(ValueError):
        respond(db, llm, "x" * 4001, config=config)
    assert llm.prompts == []


def test_blank_message_is_refused_before_any_call(db, config):
    llm = FakeProvider([ASK])
    with pytest.raises(ValueError):
        respond(db, llm, "   ", config=config)
    assert llm.prompts == []


def test_window_is_the_last_twenty_turns(db, config):
    conv = get_or_create_conversation(db)
    for i in range(30):
        db.add(Turn(conversation_id=conv.id, role="user" if i % 2 else "counselor", text=f"t{i}"))
    db.commit()
    llm = FakeProvider([ASK])
    respond(db, llm, "now", config=config)
    assert "USER: t9" not in llm.prompts[0] and "COUNSELOR: t10" not in llm.prompts[0]
    assert "USER: t11" in llm.prompts[0] and "USER: now" in llm.prompts[0]


def test_endless_look_ups_end_in_an_apology_after_a_bounded_number_of_calls(db, config):
    lk = {"action": "look_up", "tool": "gap", "args": {"skill": "Docker"}}
    llm = FakeProvider([lk] * 20)
    reply = respond(db, llm, "hi", config=config)
    assert reply.turn is None and reply.text == APOLOGY
    assert len(llm.prompts) == 6
    assert [t.role for t in db.exec(select(Turn)).all()] == ["user"]
