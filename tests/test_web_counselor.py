import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.counselor import tools
from jobhunt.counselor.models import CounselorNote, Turn
from jobhunt.db import create_engine_for, init_db
from jobhunt.llm import FakeProvider
from jobhunt.profile.models import EvidenceAtom
from jobhunt.web.app import create_app
from jobhunt.web.deps import get_llm

ASK = {"action": "ask", "text": "What did you build last?", "quick_replies": ["A web app", "A CLI"]}
PROPOSE = {
    "action": "propose_atom",
    "text": "I'd record this:",
    "atom": {
        "kind": "project",
        "title": "Shortener",
        "description": "Flask.",
        "skills": ["Python"],
    },
    "because": "you said 'a URL shortener in Flask'",
}


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "fetch_repos", lambda u, token=None: [])

    def _make(responses):
        cfg = Config(
            profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"),
            llm=LLMConfig(cache_dir=tmp_path / "cache"),
            data_dir=tmp_path,
        )
        app = create_app(cfg)
        provider = FakeProvider(responses)
        app.dependency_overrides[get_llm] = lambda: provider
        client = TestClient(app)
        client.engine = create_engine_for(tmp_path / "jobhunt.db")
        init_db(client.engine)
        return client

    return _make


def test_first_visit_runs_the_opener(make_client):
    client = make_client([ASK])
    page = client.get("/counselor")
    assert page.status_code == 200
    assert "What did you build last?" in page.text
    assert 'hx-vals=\'{"text": "A web app"}\'' in page.text
    with Session(client.engine) as db:
        assert [t.role for t in db.exec(select(Turn)).all()] == ["counselor"]


def test_say_appends_both_turns_and_refreshes_chips(make_client):
    client = make_client(
        [ASK, {"action": "ask", "text": "How many users?", "quick_replies": ["Under ten"]}]
    )
    client.get("/counselor")
    partial = client.post(
        "/counselor/say", data={"text": "A URL shortener."}, headers={"HX-Request": "true"}
    )
    assert partial.status_code == 200
    assert "A URL shortener." in partial.text and "How many users?" in partial.text
    assert 'id="chips" hx-swap-oob="true"' in partial.text and '"text": "Under ten"' in partial.text
    assert "A web app" not in partial.text


def test_over_length_message_is_a_400(make_client):
    client = make_client([ASK])
    client.get("/counselor")
    assert client.post("/counselor/say", data={"text": "x" * 4001}).status_code == 400


def test_proposal_card_accept_with_edit_writes_an_atom(make_client):
    client = make_client([ASK, PROPOSE])
    client.get("/counselor")
    partial = client.post("/counselor/say", data={"text": "a URL shortener in Flask"})
    assert (
        "record this:" in partial.text
        and 'name="title"' in partial.text
        and "you said" in partial.text
    )

    with Session(client.engine) as db:
        turn_id = db.exec(select(Turn).where(Turn.proposal_state == "open")).one().id
    card = client.post(
        f"/counselor/proposals/{turn_id}/accept",
        data={"title": "URL shortener", "skills": "Python, Flask"},
    )
    assert (
        card.status_code == 200
        and "Recorded:" in card.text
        and "URL shortener" in card.text
        and "/atoms#atom-" in card.text
    )
    with Session(client.engine) as db:
        atom = db.exec(select(EvidenceAtom)).one()
        assert (atom.title, atom.skills, atom.confirmed, atom.source_ref) == (
            "URL shortener",
            ["Python", "Flask"],
            False,
            f"counselor:{turn_id}",
        )


def test_reject_marks_the_card_and_the_counselor_answers(make_client):
    client = make_client([ASK, PROPOSE, {"action": "ask", "text": "What did I get wrong?"}])
    client.get("/counselor")
    client.post("/counselor/say", data={"text": "a URL shortener in Flask"})
    with Session(client.engine) as db:
        turn_id = db.exec(select(Turn).where(Turn.proposal_state == "open")).one().id
    partial = client.post(f"/counselor/proposals/{turn_id}/reject")
    assert "Not recorded" in partial.text and "What did I get wrong?" in partial.text
    with Session(client.engine) as db:
        assert db.get(Turn, turn_id).proposal_state == "rejected"
        assert db.exec(select(EvidenceAtom)).all() == []


def test_accept_twice_is_409_and_unknown_is_404(make_client):
    client = make_client([ASK, PROPOSE])
    client.get("/counselor")
    client.post("/counselor/say", data={"text": "x"})
    with Session(client.engine) as db:
        turn_id = db.exec(select(Turn).where(Turn.proposal_state == "open")).one().id
    client.post(f"/counselor/proposals/{turn_id}/accept")
    assert client.post(f"/counselor/proposals/{turn_id}/accept").status_code == 409
    assert client.post("/counselor/proposals/999/accept").status_code == 404


def test_notes_are_listed_and_closable(make_client):
    client = make_client(
        [
            ASK,
            {
                "action": "note",
                "kind": "goal",
                "text": "wants ML",
                "then": {"action": "ask", "text": "Why?"},
            },
        ]
    )
    client.get("/counselor")
    partial = client.post("/counselor/say", data={"text": "I want ML."})
    assert "noted: wants ML" in partial.text
    page = client.get("/counselor")
    assert "What I've noted about you" in page.text and "wants ML" in page.text
    with Session(client.engine) as db:
        note_id = db.exec(select(CounselorNote)).one().id
    notes = client.post(f"/counselor/notes/{note_id}/close")
    assert "wants ML" not in notes.text
    with Session(client.engine) as db:
        assert db.get(CounselorNote, note_id).status == "closed"


def test_end_sitting_shows_the_closing_card_and_keep_going_reopens(make_client):
    client = make_client(
        [
            ASK,
            {"action": "end_sitting", "summary": "Good place to stop.", "next_time": "thesis"},
            {"action": "ask", "text": "Back to the thesis?"},
        ]
    )
    client.get("/counselor")
    partial = client.post("/counselor/stop")
    assert "Good place to stop." in partial.text and "Keep going" in partial.text
    again = client.post("/counselor/continue")
    assert "Back to the thesis?" in again.text


def test_apology_is_shown_but_not_stored(make_client):
    client = make_client([ASK, "garbage", "garbage"])
    client.get("/counselor")
    partial = client.post("/counselor/say", data={"text": "hi"})
    assert "I lost my train of thought" in partial.text
    with Session(client.engine) as db:
        assert [t.role for t in db.exec(select(Turn).order_by(Turn.id)).all()] == [
            "counselor",
            "user",
        ]


def test_provider_failure_is_a_502(make_client):
    client = make_client([ASK])
    client.get("/counselor")
    assert client.post("/counselor/say", data={"text": "hi"}).status_code == 502
