from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from jobhunt.answers import StandardAnswers, save_answers
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


def test_daily_adds_easy_apply_brief_and_skips_form_plan(engine, monkeypatch, tmp_path):
    fake_tailor(monkeypatch, engine)
    with Session(engine) as s:
        s.get(Job, 1).url = "https://www.linkedin.com/jobs/view/1"
        s.commit()
    save_answers(
        tmp_path / "answers.yaml",
        StandardAnswers(
            work_authorization="EU citizen",
            needs_sponsorship_outside_eu=False,
            notice_period="1 month",
            salary_min_eur=60000,
            salary_max_eur=75000,
            relocation=True,
            remote=False,
        ),
    )
    seen = []
    monkeypatch.setattr(daily, "prepare_plan", lambda s, a, c, llm, http: seen.append(a.id))
    config = CONFIG.model_copy(update={"answers_path": tmp_path / "answers.yaml"})
    client = FakeClient()
    daily.run_daily(config, engine=engine, llm=LLM, client=client, scan=scan_ok, now=NOW)
    assert seen == [2]
    assert "LinkedIn Easy Apply" in client.sent[0][0]
    assert "LinkedIn Easy Apply" not in client.sent[1][0]


def _outreach(engine, text="Hi, I built X for the Dev1 role at Co1."):
    from jobhunt.outreach.models import Contact, Outreach

    with Session(engine) as s:
        c = Contact(company="Co1", name="Bo", source="posting")
        s.add(c)
        s.commit()
        o = Outreach(job_id=1, contact_id=c.id, kind="message", text=text)
        s.add(o)
        s.commit()
        return o.id


def test_daily_drafts_and_sends_outreach(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    from jobhunt.outreach.models import Outreach

    def fake_draft(session, config, llm):
        return [session.get(Outreach, _outreach(engine))]

    monkeypatch.setattr(daily, "draft_for_run", fake_draft)
    monkeypatch.setattr(daily, "draft_followups", lambda session, config, llm, now: [])
    client = FakeClient()
    go(engine, client)
    texts = [t for t, _ in client.sent]
    assert any(t.startswith("Outreach #1 (message)") for t in texts)
    assert any(t.startswith("Hi, I built X") for t in texts)


def test_outreach_header_resent_without_copy_when_rejected(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    from jobhunt.outreach.models import Outreach

    class Picky(FakeClient):
        def send(self, text, buttons=None):
            self.sent.append((text, buttons))
            return not any("copy_text" in b for row in buttons or [] for b in row)

    monkeypatch.setattr(daily, "draft_for_run", lambda s, c, l: [s.get(Outreach, _outreach(engine))])
    monkeypatch.setattr(daily, "draft_followups", lambda session, config, llm, now: [])
    client = Picky()
    go(engine, client)
    headers = [b for t, b in client.sent if t.startswith("Outreach #1")]
    assert len(headers) == 2
    assert all("copy_text" not in b for row in headers[1] for b in row)
    assert headers[1][-1][0]["callback_data"] == "osent:1"


def test_outreach_failure_does_not_stop_the_digest(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)

    def boom(*a):
        raise RuntimeError("llm down")

    monkeypatch.setattr(daily, "draft_for_run", boom)
    client = FakeClient()
    out = go(engine, client)
    assert client.sent[-1] == (out, None)


def test_paused_daily_drafts_no_outreach(engine, monkeypatch):
    fake_tailor(monkeypatch, engine)
    calls = []
    monkeypatch.setattr(daily, "draft_for_run", lambda *a: calls.append(1) or [])
    monkeypatch.setattr(daily, "draft_followups", lambda *a: calls.append(1) or [])
    with Session(engine) as s:
        set_paused(s, True)
    go(engine, FakeClient())
    assert calls == []


def test_daily_growth_failure_does_not_stop_digest(engine, monkeypatch):
    monkeypatch.setattr(daily, "is_paused", lambda s: False)
    monkeypatch.setattr(daily, "refresh", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(daily, "tailor_shortlist", lambda s, l, c: TailorRun(queued=[], stopped=None))
    monkeypatch.setattr(daily, "draft_for_run", lambda *a: [])
    monkeypatch.setattr(daily, "draft_followups", lambda *a: [])
    cfg = CONFIG.model_copy(update={"profile": CONFIG.profile.model_copy(update={"github_username": "ada"})})
    client = FakeClient()
    out = daily.run_daily(cfg, engine=engine, llm=LLM, client=client, scan=scan_ok, now=NOW)
    assert out.startswith("jobhunt daily") and client.sent[-1][0] == out


def test_daily_sends_new_suggestions_and_skips_without_username(engine, monkeypatch):
    from jobhunt.growth.models import Suggestion

    monkeypatch.setattr(daily, "tailor_shortlist", lambda s, l, c: TailorRun(queued=[], stopped=None))
    monkeypatch.setattr(daily, "draft_for_run", lambda *a: [])
    monkeypatch.setattr(daily, "draft_followups", lambda *a: [])
    monkeypatch.setattr(daily, "Gh", lambda: object())
    monkeypatch.setattr(daily, "refresh", lambda *a, **k: [Suggestion(id=9, kind="pin", target="ada/x", title="t", body="b")])
    cfg = CONFIG.model_copy(update={"profile": CONFIG.profile.model_copy(update={"github_username": "ada"})})
    client = FakeClient()
    daily.run_daily(cfg, engine=engine, llm=None, client=client, scan=scan_ok, now=NOW)
    assert client.sent[0][0] == "#9 Pin ada/x on your GitHub profile: b"
    client = FakeClient()
    go(engine, client, llm=None)  # no username: nothing growth-related
    assert all("Pin ada/x" not in t for t, _ in client.sent)


def test_readme_drafts_only_for_thin_readmes(engine, monkeypatch):
    from types import SimpleNamespace

    from jobhunt.growth.models import upsert_suggestion

    with Session(engine) as s:
        upsert_suggestion(s, "pin", "ada/thin", title="t")
        upsert_suggestion(s, "pin", "ada/rich", title="t")
    drafted = []

    class Gh:
        def readme(self, full):
            return ("x" * 1000 if full == "ada/rich" else "", None)

        def repos(self, *a):
            return []

    monkeypatch.setattr(daily, "Gh", Gh)
    monkeypatch.setattr(daily, "refresh", lambda *a, **k: [])
    monkeypatch.setattr(daily, "draft_repo_readme", lambda s, gh, t, *a: drafted.append(t))
    monkeypatch.setattr(daily, "draft_profile_readme", lambda *a: None)
    cfg = CONFIG.model_copy(deep=True)
    cfg.profile.github_username = "ada"
    fake_tailor(monkeypatch, engine)
    daily.run_daily(cfg, engine=engine, llm=SimpleNamespace(), client=None, scan=scan_ok, now=NOW)
    assert drafted == ["ada/thin"]


def _grow_cfg():
    cfg = CONFIG.model_copy(deep=True)
    cfg.profile.github_username = "ada"
    return cfg


def test_grow_sends_created_rows_even_when_a_later_step_fails_and_marks_shown(engine, monkeypatch):
    from types import SimpleNamespace

    from jobhunt.growth.gh import GhError
    from jobhunt.growth.models import Suggestion, upsert_suggestion

    with Session(engine) as s:
        draft = upsert_suggestion(s, "readme", "ada/x", title="t", body="# x\n")
        draft_id = draft.id

    class Gh:
        def repos(self, *a):
            raise GhError("offline")

        def readme(self, full):
            return "", None

    monkeypatch.setattr(daily, "Gh", Gh)
    monkeypatch.setattr(daily, "refresh", lambda *a, **k: [draft])
    cfg = _grow_cfg()
    for ok in (False, True):
        client = FakeClient(ok=ok)
        with Session(engine) as s:
            daily._grow(s, cfg, SimpleNamespace(), client, NOW)
            row = s.get(Suggestion, draft_id)
            assert len(client.sent) == 1 and client.sent[0][0].startswith(f"#{draft_id} README draft")
            assert (row.shown_at is not None) is ok


def test_profile_readme_not_drafted_over_a_real_one_and_repo_drafts_capped(engine, monkeypatch):
    from types import SimpleNamespace

    from jobhunt.growth.models import upsert_suggestion

    with Session(engine) as s:
        for i in range(5):
            upsert_suggestion(s, "pin", f"ada/r{i}", title="t")
    drafted, profile = [], []

    class Gh:
        def readme(self, full):
            return ("x" * 500 if full == "ada/ada" else "", None)

        def repos(self, *a):
            return []

    monkeypatch.setattr(daily, "Gh", Gh)
    monkeypatch.setattr(daily, "refresh", lambda *a, **k: [])
    monkeypatch.setattr(daily, "draft_repo_readme", lambda s, gh, t, *a: drafted.append(t))
    monkeypatch.setattr(daily, "draft_profile_readme", lambda *a: profile.append(1))
    with Session(engine) as s:
        daily._grow(s, _grow_cfg(), SimpleNamespace(), None, NOW)
    assert len(drafted) == daily.MAX_README_DRAFTS == 3 and profile == []



def test_unsent_readme_draft_is_sent_again(engine, monkeypatch):
    from jobhunt.growth.models import Suggestion, upsert_suggestion

    with Session(engine) as s:
        sid = upsert_suggestion(s, "readme", "ada/r", title="t", body="# r").id

    class Gh:
        def readme(self, full):
            return ("", None)

        def repos(self, *a):
            return []

    monkeypatch.setattr(daily, "Gh", Gh)
    monkeypatch.setattr(daily, "refresh", lambda *a, **k: [])
    cfg = CONFIG.model_copy(deep=True)
    cfg.profile.github_username = "ada"
    fake_tailor(monkeypatch, engine)
    client = FakeClient()
    daily.run_daily(cfg, engine=engine, llm=None, client=client, scan=scan_ok, now=NOW)
    assert any("README draft for ada/r" in text for text, _ in client.sent)
    with Session(engine) as s:
        assert s.get(Suggestion, sid).shown_at is not None
