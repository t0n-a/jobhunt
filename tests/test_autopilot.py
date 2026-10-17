from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from jobhunt.apply.ats import detect_ats
from jobhunt.apply.autopilot import autopilot_on, autosubmit, gate, set_autopilot
from jobhunt.apply.browser import FormRun
from jobhunt.apply.form import FormField
from jobhunt.apply.plan import FormPlan
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
    block_host(session, "boards.greenhouse.io", "x")
    assert check(session, app, cfg) == "blocked host"
    from jobhunt.state import BlockedHost

    session.delete(session.get(BlockedHost, "boards.greenhouse.io"))
    session.commit()
    assert check(session, app, cfg, plan=None) == "form not fully answered"
    hard = FormPlan(unknown=[FormField(name="q", label="Why?", kind="text", required=True)])
    assert check(session, app, cfg, plan=hard) == "form not fully answered"
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
    assert url == URL.format(n=1)
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
    runner = Runner(FormRun("error", "no confirmation"))
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
