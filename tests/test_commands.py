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
        s.add(Application(id=1, job_id=1, status="queued"))
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
