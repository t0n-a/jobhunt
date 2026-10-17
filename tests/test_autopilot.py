from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from jobhunt.apply.ats import detect_ats
from jobhunt.apply.autopilot import autopilot_on, autosubmit, gate, set_autopilot
from jobhunt.apply.browser import FormRun
from jobhunt.apply.form import FormField
from jobhunt.apply.plan import FormPlan, Planned
from jobhunt.config import AutopilotConfig, Config, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, Score
from jobhunt.state import block_host, set_paused
from jobhunt.tailor.models import Application

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
URL = "https://boards.greenhouse.io/acme/jobs/{n}"


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "a.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def config(tmp_path, **kw) -> Config:
    kw.setdefault("trust_target", 0)
    return Config(
        profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"),
        data_dir=tmp_path,
        autopilot=AutopilotConfig(**kw),
    )


def add(session, n=1, fit=90, status="queued", plan=None, url=None, channel="manual"):
    session.add(
        Job(id=n, title=f"Dev{n}", company=f"Co{n}", description="d", url=url or URL.format(n=n),
            content_hash=str(n))
    )
    session.add(
        Score(job_id=n, fit_score=fit, seniority_fit="ok", track="backend", rationale="r", content_hash=str(n))
    )
    plan = plan if plan is not None else FormPlan().to_json()
    session.add(Application(id=n, job_id=n, status=status, form_plan=plan, channel=channel))
    session.commit()
    return session.get(Application, n)


class Runner:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, url, plan, **kw):
        self.calls.append((url, kw))
        return self.results.pop(0) if self.results else FormRun("submitted")


def check(session, application, cfg, plan="same", **kw):
    job = session.get(Job, application.job_id)
    score = session.get(Score, 1) if kw.pop("score", True) else None
    if plan == "same":
        plan = FormPlan.from_json(application.form_plan) if application.form_plan else None
    return gate(
        session, application, job=job, score=score, plan=plan,
        target=kw.pop("target", detect_ats(job.url)), cfg=cfg, now=NOW,
    )


def test_cannot_switch_on_before_trust(session):
    cfg = AutopilotConfig(trust_target=3)
    assert set_autopilot(session, True, cfg) == (
        "Not yet: autopilot needs 3 clean approvals; you have 0."
    )
    assert not autopilot_on(session)


def test_switch_on_and_off(session):
    cfg = AutopilotConfig(trust_target=0)
    assert set_autopilot(session, True, cfg) == (
        "Autopilot is on. jobhunt will submit up to 10 applications a day in your name, only on "
        "forms with no login and no captcha, for jobs with fit 85 or more. "
        'Send "autopilot off" to stop.'
    )
    assert autopilot_on(session)
    assert set_autopilot(session, False, cfg) == "Autopilot is off."
    assert not autopilot_on(session)


def test_gate_reasons_in_order(session):
    cfg = AutopilotConfig(trust_target=1)
    app = add(session)
    assert check(session, app, cfg) == "autopilot off"
    set_autopilot(session, False, cfg)
    from jobhunt.state import set_state

    set_state(session, "autopilot", "1")
    set_paused(session, True)
    assert check(session, app, cfg) == "paused"
    set_paused(session, False)
    assert check(session, app, cfg) == "trust not earned"
    app.trust_credit = True
    session.add(app)
    session.commit()
    app.status = "approved"
    assert check(session, app, cfg) == "not queued"
    app.status = "queued"
    assert check(session, app, cfg, score=False) == "fit ? < 85"
    session.get(Score, 1).fit_score = 80
    assert check(session, app, cfg) == "fit 80 < 85"
    session.get(Score, 1).fit_score = 90
    assert check(session, app, cfg, target=None) == "unsupported form"
    assert check(session, app, AutopilotConfig(trust_target=1, ats_allowlist=["lever"])) == (
        "unsupported form"
    )
    block_host(session, "job-boards.greenhouse.io", "x")
    assert check(session, app, cfg) == "blocked host"
    from jobhunt.state import BlockedHost

    session.delete(session.get(BlockedHost, "job-boards.greenhouse.io"))
    session.commit()
    assert check(session, app, cfg, plan=None) == "form not fully answered"
    hard = FormPlan(unknown=[FormField(name="q", label="Why?", kind="text", required=True)])
    assert check(session, app, cfg, plan=hard) == "form not fully answered"
    drafted = FormPlan(filled=[Planned("q", "I led it.", "evidence")])
    assert check(session, app, cfg, plan=drafted) == "has answers drafted from evidence"
    assert check(session, app, cfg) is None
    cap = AutopilotConfig(trust_target=1, daily_cap=0)
    assert check(session, app, cap) == "daily cap reached"


def go(session, tmp_path, runner, **kw):
    cfg = config(tmp_path, **kw)
    set_autopilot(session, True, cfg.autopilot)
    return autosubmit(session, cfg, runner=runner, now=NOW)


def test_submitted_is_sent_with_autopilot_channel(session, tmp_path):
    app = add(session)
    runner = Runner(FormRun("submitted"))
    [(done, run)] = go(session, tmp_path, runner)
    assert (done.status, done.channel, run.status) == ("sent", "autopilot", "submitted")
    url, kw = runner.calls[0]
    assert url == URL.format(n=1).replace("//boards.", "//job-boards.")
    assert kw["headless"] and kw["submit"] and not kw["allow_obstacles"]
    assert kw["screenshot_dir"] == tmp_path / "screenshots"
    assert app.applied_at is not None


def test_stopped_requeues_with_reason(session, tmp_path):
    from sqlmodel import select

    from jobhunt.tailor.models import ApplicationEvent

    app = add(session)
    go(session, tmp_path, Runner(FormRun("stopped", "captcha")))
    assert (app.status, app.channel) == ("queued", "autopilot")
    notes = [e.note for e in session.exec(select(ApplicationEvent).order_by(ApplicationEvent.id))]
    assert notes[-1] == "autopilot stopped: captcha"


def test_error_requeues_and_blocks_host(session, tmp_path):
    from sqlmodel import select

    from jobhunt.tailor.models import ApplicationEvent

    app = add(session)
    add(session, 2, url="https://boards.greenhouse.io/acme/jobs/2")
    runner = Runner(FormRun("error", "no confirmation", clicked=True))
    go(session, tmp_path, runner)
    assert len(runner.calls) == 1  # the host block stops the second job in the same run
    assert app.status == "queued"
    notes = [e.note for e in session.exec(select(ApplicationEvent).order_by(ApplicationEvent.id))]
    assert "autopilot error: no confirmation - it may have gone through; check before applying again" in notes
    assert autosubmit(session, config(tmp_path), runner=runner, now=NOW) == []
    assert len(runner.calls) == 1
    assert check(session, session.get(Application, 2), AutopilotConfig(trust_target=0)) == (
        "blocked host"
    )


def test_runner_crash_is_an_error(session, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("x")

    app = add(session)
    go(session, tmp_path, boom)
    assert app.status == "queued"


def test_daily_cap_stops_the_run(session, tmp_path):
    add(session, 1)
    add(session, 2, url="https://job-boards.greenhouse.io/other/jobs/2")
    runner = Runner()
    go(session, tmp_path, runner, daily_cap=1)
    assert len(runner.calls) == 1


def test_yesterdays_sends_do_not_count(session, tmp_path):
    add(session, 1)
    runner = Runner()
    cfg = config(tmp_path, daily_cap=1)
    set_autopilot(session, True, cfg.autopilot)
    autosubmit(session, cfg, runner=runner, now=NOW - timedelta(days=1))
    add(session, 2, url="https://job-boards.greenhouse.io/other/jobs/2")
    autosubmit(session, cfg, runner=runner, now=NOW)
    assert len(runner.calls) == 2


def test_paused_submits_nothing(session, tmp_path):
    add(session)
    cfg = config(tmp_path)
    set_autopilot(session, True, cfg.autopilot)
    set_paused(session, True)
    runner = Runner()
    assert autosubmit(session, cfg, runner=runner, now=NOW) == []
    assert runner.calls == []


def test_wave2_approved_rows_are_never_autosubmitted(session, tmp_path):
    add(session, status="approved", channel="manual")
    runner = Runner()
    go(session, tmp_path, runner)
    assert runner.calls == []


def test_a_stopped_form_is_not_retried_the_next_day(session, tmp_path):
    add(session)
    runner = Runner(FormRun("stopped", "obstacle: captcha"))
    go(session, tmp_path, runner)
    go(session, tmp_path, runner)
    assert len(runner.calls) == 1


def _notes(session):
    from sqlmodel import select

    from jobhunt.tailor.models import ApplicationEvent

    return [e.note for e in session.exec(select(ApplicationEvent).order_by(ApplicationEvent.id))]


MAYBE = "it may have gone through; check before applying again"


def test_marked_approved_with_a_note_before_the_runner_runs(session, tmp_path):
    app = add(session)
    seen = {}

    def runner(url, plan, **kw):
        seen["status"], seen["notes"] = app.status, _notes(session)
        return FormRun("submitted")

    go(session, tmp_path, runner)
    assert seen["status"] == "approved" and seen["notes"][-1] == "autopilot: submitting"


def test_runner_crash_note_says_it_may_have_gone_through(session, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("x")

    add(session)
    go(session, tmp_path, boom)
    assert _notes(session)[-1] == f"autopilot error: RuntimeError - {MAYBE}"


def test_any_error_result_says_it_may_have_gone_through(session, tmp_path):
    add(session)
    go(session, tmp_path, Runner(FormRun("error", "TimeoutError", clicked=True)))
    assert _notes(session)[-1] == f"autopilot error: TimeoutError - {MAYBE}"


def test_error_before_the_click_is_transient_without_a_warning(session, tmp_path):
    from jobhunt.state import BlockedHost

    add(session)
    go(session, tmp_path, Runner(FormRun("error", "Error")))
    assert _notes(session)[-1] == "autopilot error: Error"
    row = session.get(BlockedHost, "job-boards.greenhouse.io")
    assert row.until is not None and row.until.replace(tzinfo=UTC) - NOW <= timedelta(hours=24)


def test_status_changed_during_the_run_is_not_overwritten(session, tmp_path):
    app = add(session)

    def runner(url, plan, **kw):
        session.connection().exec_driver_sql("update application set status='sent' where id=1")
        return FormRun("stopped", "captcha")

    go(session, tmp_path, runner)
    assert session.get(Application, 1).status == "sent"
    assert app.id == 1


def test_block_expiry_by_cause(session, tmp_path):
    from jobhunt.state import BlockedHost

    add(session)
    go(session, tmp_path, Runner(FormRun("error", "http 429")))
    row = session.get(BlockedHost, "job-boards.greenhouse.io")
    assert row.until is not None and timedelta(hours=23) < row.until.replace(tzinfo=UTC) - NOW <= timedelta(hours=24)


def test_no_confirmation_block_is_permanent(session, tmp_path):
    from jobhunt.state import BlockedHost

    add(session)
    go(session, tmp_path, Runner(FormRun("error", "no confirmation", clicked=True)))
    assert session.get(BlockedHost, "job-boards.greenhouse.io").until is None


def test_expired_block_is_ignored(session):
    from jobhunt.state import is_blocked

    block_host(session, "h.example", "http 429", until=datetime.now(UTC) - timedelta(hours=1))
    assert not is_blocked(session, "h.example")
    block_host(session, "h.example", "http 429", until=datetime.now(UTC) + timedelta(hours=1))
    assert is_blocked(session, "h.example")


def test_row_taken_by_another_run_is_not_submitted(session, tmp_path, monkeypatch):
    import jobhunt.apply.autopilot as ap

    add(session)
    real = ap.gate

    def gate_then_steal(*a, **k):
        r = real(*a, **k)
        session.connection().exec_driver_sql("update application set status='approved' where id=1")
        return r

    monkeypatch.setattr(ap, "gate", gate_then_steal)
    runner = Runner()
    go(session, tmp_path, runner)
    assert not runner.calls
