from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from jobhunt.apply import commands
from jobhunt.apply.commands import HELP, run_tick
from jobhunt.apply.followup import followups_due
from jobhunt.apply.queue import trust_count
from jobhunt.apply.tracker import set_status
from jobhunt.config import Config, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job
from jobhunt.state import get_state, is_paused
from jobhunt.tailor.models import Application

NOW = datetime(2026, 10, 17, tzinfo=UTC)
CONFIG = Config(profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"))


class FakeClient:
    chat_id = "5"

    def __init__(self, updates=()):
        self.queued = list(updates)
        self.sent: list[str] = []
        self.answered: list[str] = []

    def updates(self, offset):
        out, self.queued = self.queued, []
        return out

    def send(self, text, buttons=None):
        self.sent.append(text)
        return True

    def answer_callback(self, callback_id):
        self.answered.append(callback_id)


def msg(update_id, text, chat="5"):
    return {"update_id": update_id, "message": {"chat": {"id": int(chat)}, "text": text}}


def cb(update_id, data, chat="5"):
    return {
        "update_id": update_id,
        "callback_query": {"id": f"c{update_id}", "data": data, "message": {"chat": {"id": int(chat)}}},
    }


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        s.add(Job(id=1, title="Dev", company="A", description="d", url="http://job", content_hash="h"))
        s.add(Application(id=1, job_id=1, status="queued", reviewed_at=NOW))
        s.add(Application(id=2, job_id=1, status="queued"))
        s.commit()
        yield s


def tick(session, *updates):
    client = FakeClient(updates)
    return run_tick(session, client, CONFIG, NOW), client


def test_yes_approves_and_replies_with_the_link(session):
    n, client = tick(session, msg(1, "yes 1"))
    assert n == 1
    assert session.get(Application, 1).status == "approved"
    assert client.sent[0].startswith("Approved #1. Apply here: http://job\n")
    assert 'Send "sent 1"' in client.sent[0]


def test_yes_twice_counts_once(session):
    _, client = tick(session, cb(1, "yes:1"), cb(2, "yes:1"))
    assert trust_count(session) == 1
    assert client.sent[1] == "#1 is approved; nothing changed."
    assert client.answered == ["c1", "c2"]


def test_updates_from_other_chats_are_ignored(session):
    n, client = tick(session, msg(4, "yes 1", chat="999"))
    assert n == 0 and client.sent == []
    assert session.get(Application, 1).status == "queued"
    assert get_state(session, "telegram_offset") == "5"


def test_failing_command_still_advances_offset(session, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(commands, "handle_command", boom)
    _, client = tick(session, msg(9, "yes 1"))
    assert client.sent[0].startswith("That didn't work")
    assert get_state(session, "telegram_offset") == "10"
    _, again = tick(session)
    assert again.sent == []


def test_pause_and_resume(session):
    tick(session, msg(1, "pause"))
    assert is_paused(session)
    tick(session, msg(2, "resume"))
    assert not is_paused(session)


def test_sent_marks_sent(session):
    _, client = tick(session, msg(1, "sent 1"))
    assert client.sent == ["Marked #1 as sent."]
    assert session.get(Application, 1).status == "sent"


def test_done_records_followup(session):
    set_status(session, session.get(Application, 1), "sent", now=NOW - timedelta(days=8))
    assert followups_due(session, NOW)
    _, client = tick(session, msg(1, "done 1"))
    assert client.sent == ["Noted the follow-up on #1."]
    assert not followups_due(session, NOW)


def test_unknown_text_gets_help(session):
    _, client = tick(session, msg(1, "hello"))
    assert client.sent == [HELP]


@pytest.mark.parametrize(
    "verb,start,ok",
    [
        ("replied", "sent", True),
        ("replied", "queued", False),
        ("replied", "interview", False),
        ("interview", "sent", True),
        ("interview", "replied", True),
        ("interview", "offer", False),
        ("offer", "interview", True),
        ("offer", "sent", False),
        ("rejected", "sent", True),
        ("rejected", "replied", True),
        ("rejected", "interview", True),
        ("rejected", "queued", False),
        ("rejected", "offer", False),
    ],
)
def test_outcome_verbs_follow_the_transition_table(session, verb, start, ok):
    application = session.get(Application, 1)
    if start != "queued":
        set_status(session, application, start, now=NOW - timedelta(days=1))
    _, client = tick(session, msg(1, f"{verb} 1"))
    if ok:
        assert client.sent == [f"Marked #1 as {verb}."]
        assert session.get(Application, 1).status == verb
    else:
        assert client.sent == [f"#1 is {start}; nothing changed."]
        assert session.get(Application, 1).status == start


def test_a_rejected_application_is_not_nudged_or_ghosted(session):
    from jobhunt.apply.followup import mark_cold

    set_status(session, session.get(Application, 1), "sent", now=NOW - timedelta(days=30))
    tick(session, msg(1, "rejected 1"))
    assert not followups_due(session, NOW)
    assert mark_cold(session, NOW) == []
    assert session.get(Application, 1).status == "rejected"


def test_help_lists_the_outcome_verbs():
    for verb in ("replied N", "interview N", "offer N", "rejected N"):
        assert verb in HELP


def test_yes_without_a_review_earns_no_trust_and_says_so(session):
    _, client = tick(session, msg(1, "yes 2"))
    assert trust_count(session) == 0
    assert client.sent[0].endswith("\nOpen the review page before approving to count toward trust.")


def test_yes_after_a_review_earns_trust(session):
    _, client = tick(session, msg(1, "yes 1"))
    assert trust_count(session) == 1
    assert "Open the review page" not in client.sent[0]


def test_yes_reply_points_at_the_pdf_only_when_there_is_one(session):
    _, client = tick(session, msg(1, "yes 1"))
    assert "Review (no PDF yet; render it there): http://127.0.0.1:8000/applications/1" in client.sent[0]
    session.get(Application, 2).pdf_path = "x.pdf"
    session.commit()
    _, client = tick(session, msg(2, "yes 2"))
    assert "PDF and answers: http://127.0.0.1:8000/applications/2" in client.sent[0]


def test_yes_mentions_assisted_apply_when_planned(session):
    from jobhunt.apply.form import FormField
    from jobhunt.apply.plan import FormPlan

    app = session.get(Application, 1)
    app.form_plan = FormPlan(unknown=[FormField("q", "Why?", "text", True)]).to_json()
    session.add(app)
    session.commit()
    _, client = tick(session, msg(1, "yes 1"))
    assert "At your computer: jobhunt apply 1 opens the form already filled." in client.sent[0]
    assert "(1 question(s) left for you.)" in client.sent[0]
