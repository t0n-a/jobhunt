from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from jobhunt.apply import daily
from jobhunt.apply.queue import TailorRun
from jobhunt.apply.tracker import add_event, set_status
from jobhunt.config import Config, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job
from jobhunt.scan import ScanResult
from jobhunt.state import set_paused
from jobhunt.tailor.models import Application

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
CONFIG = Config(profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"))


class FakeClient:
    def __init__(self, ok=True):
        self.sent = []
        self.ok = ok

    def send(self, text, buttons=None):
        self.sent.append((text, buttons))
        return self.ok


@pytest.fixture
def engine(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        for n in (1, 2):
            s.add(Job(id=n, title=f"Dev{n}", company=f"Co{n}", description="d", url="u", content_hash=str(n)))
            s.add(Application(id=n, job_id=n, status="queued"))
        s.commit()
    return engine


def scan_ok():
    return ScanResult()


LLM = object()


def go(engine, client, scan=scan_ok, llm=LLM):
    return daily.run_daily(CONFIG, engine=engine, llm=llm, client=client, scan=scan, now=NOW)


def fake_tailor(monkeypatch, engine, stopped=None):
    calls = []

    def fake(session, llm, config):
        calls.append(1)
        return TailorRun(queued=[session.get(Application, 1), session.get(Application, 2)], stopped=stopped)

    monkeypatch.setattr(daily, "tailor_shortlist", fake)
    return calls


def test_daily_queues_and_sends_digest(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    client = FakeClient()
    out = go(engine, client)
    assert len(client.sent) == 3
    assert client.sent[0][1] and client.sent[1][1]
    assert client.sent[2] == (out, None)


def test_paused_daily_tailors_nothing(engine, monkeypatch):
    calls = fake_tailor(monkeypatch, engine)
    with Session(engine) as s:
        set_paused(s, True)
    out = go(engine, FakeClient())
    assert calls == []
    assert out.split("\n")[1].startswith("Paused")


def test_tailoring_stopped_is_said(engine, monkeypatch):
    fake_tailor(monkeypatch, engine, stopped="no evidence")
    assert go(engine, None).split("\n")[2] == "Tailoring stopped: no evidence"


def test_daily_runs_without_telegram(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    assert go(engine, None).startswith("jobhunt daily")


def test_telegram_down_does_not_break_the_run(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    assert go(engine, FakeClient(ok=False)).startswith("jobhunt daily")


def test_scan_failure_is_reported_not_raised(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)

    def boom():
        raise RuntimeError("down")

    assert "Scan failed: down" in go(engine, None, scan=boom)


def test_linkedin_blocked_is_said(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    out = go(engine, None, scan=lambda: ScanResult(failed_sources=["linkedin"]))
    assert "LinkedIn was skipped" in out


def test_cold_applications_are_ghosted(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    with Session(engine) as s:
        app = s.get(Application, 1)
        set_status(s, app, "sent", now=NOW - timedelta(days=30))
        add_event(s, app, "followup", now=NOW - timedelta(days=20))
        add_event(s, app, "followup", now=NOW - timedelta(days=8))
    out = go(engine, None)
    assert "Marked ghosted: 1" in out
    with Session(engine) as s:
        assert s.get(Application, 1).status == "ghosted"


def test_a_tailoring_crash_still_sends_the_digest(engine, monkeypatch):
    def boom(session, llm, config):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(daily, "tailor_shortlist", boom)
    client = FakeClient()
    out = go(engine, client)
    assert "Tailoring failed: RuntimeError" in out
    assert client.sent[-1] == (out, None)


def test_no_llm_skips_tailoring_but_sends_the_digest(engine, monkeypatch):
    calls = fake_tailor(monkeypatch, engine)
    client = FakeClient()
    digest = go(engine, client, llm=None)
    assert calls == []
    assert "Tailoring stopped: No LLM key; tailoring skipped. Run jobhunt setup." in digest
    assert client.sent


def test_daily_prepares_plans_for_new_queue_items(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    seen = []
    monkeypatch.setattr(daily, "prepare_plan", lambda s, a, c, llm, http: seen.append(a.id))
    go(engine, FakeClient())
    assert seen == [1, 2]


def test_daily_survives_a_failing_plan(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    seen = []

    def boom(s, a, c, llm, http):
        seen.append(a.id)
        raise RuntimeError("x")

    monkeypatch.setattr(daily, "prepare_plan", boom)
    client = FakeClient()
    out = go(engine, client)
    assert seen == [1, 2] and client.sent[-1] == (out, None)


def test_autosubmit_runs_and_submitted_rows_are_not_queued(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)

    def fake(session, config, *, now):
        set_status(session, session.get(Application, 1), "sent")

    monkeypatch.setattr(daily, "autosubmit", fake)
    client = FakeClient()
    go(engine, client)
    assert len(client.sent) == 2  # one queue message + digest
    assert client.sent[0][0].startswith("#2 ")


def test_autosubmit_failure_does_not_skip_the_queue_messages(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(daily, "autosubmit", boom)
    client = FakeClient()
    go(engine, client)
    assert len(client.sent) == 3  # two queue messages + digest


def test_autosubmit_skipped_when_paused(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    called = []
    monkeypatch.setattr(daily, "autosubmit", lambda *a, **k: called.append(1))
    with Session(engine) as s:
        set_paused(s, True)
    go(engine, FakeClient())
    assert called == []


def test_stopped_note_is_in_queue_message(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    monkeypatch.setattr(daily, "autosubmit", lambda *a, **k: None)
    with Session(engine) as s:
        add_event(s, s.get(Application, 1), "queued", note="autopilot stopped: captcha")
    client = FakeClient()
    go(engine, client)
    assert client.sent[0][0].endswith("Autopilot: stopped: captcha")
    assert "Autopilot:" not in client.sent[1][0]


def test_error_note_is_in_queue_message(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    monkeypatch.setattr(daily, "autosubmit", lambda *a, **k: None)
    note = "autopilot error: Error - it may have gone through; check before applying again"
    with Session(engine) as s:
        add_event(s, s.get(Application, 1), "queued", note=note)
    client = FakeClient()
    go(engine, client)
    assert client.sent[0][0].endswith("Autopilot: error: Error - it may have gone through; check before applying again")
