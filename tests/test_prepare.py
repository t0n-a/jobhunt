from types import SimpleNamespace

import httpx
import pytest
import respx
from sqlmodel import Session

from jobhunt.apply.form import GREENHOUSE_API, FormField
from jobhunt.apply.plan import FormPlan, Planned, save_plan
from jobhunt.apply.prepare import assisted_apply, prepare_plan
from jobhunt.config import Config, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job
from jobhunt.tailor.models import Application

GH = "https://boards.greenhouse.io/acme/jobs/123"
LEVER = "https://jobs.lever.co/acme/12345678-1234-1234-1234-123456789abc"


def make(tmp_path, url):
    cfg = Config(
        profile=ProfileConfig(name="Ada Lovelace", email="a@example.com", location="Porto"),
        data_dir=tmp_path,
        answers_path=tmp_path / "none.yaml",
    )
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    s = Session(engine)
    s.add(Job(id=1, title="Dev", company="A", description="d", url=url, content_hash="h"))
    s.add(Application(id=1, job_id=1, status="approved"))
    s.commit()
    return s, cfg


@pytest.fixture
def http():
    with httpx.Client() as c:
        yield c


def lever_scan(url):
    return [FormField("email", "Email", "text", True)]


@respx.mock
def test_greenhouse_plan_saved(tmp_path, http):
    s, cfg = make(tmp_path, GH)
    body = {"questions": [{"label": "Email", "required": True,
                           "fields": [{"name": "email", "type": "input_text"}]}]}
    respx.get(GREENHOUSE_API.format(board="acme", job_id="123")).respond(200, json=body)
    plan = prepare_plan(s, s.get(Application, 1), cfg, None, http)
    assert plan.filled[0].name == "email" and plan.filled[0].value == "a@example.com"
    assert s.get(Application, 1).form_plan == plan.to_json()


def test_lever_uses_scan(tmp_path, http):
    s, cfg = make(tmp_path, LEVER)
    plan = prepare_plan(s, s.get(Application, 1), cfg, None, http, scan=lever_scan)
    assert plan.filled[0].value == "a@example.com"


def test_unsupported_url_saves_nothing(tmp_path, http):
    s, cfg = make(tmp_path, "https://example.com/job")
    assert prepare_plan(s, s.get(Application, 1), cfg, None, http) is None
    assert s.get(Application, 1).form_plan is None


def test_unreadable_form_saves_nothing(tmp_path, http):
    s, cfg = make(tmp_path, LEVER)
    assert prepare_plan(s, s.get(Application, 1), cfg, None, http, scan=lambda u: None) is None
    assert s.get(Application, 1).form_plan is None


def runner(status, reason=""):
    calls = []

    def run(url, plan, **kw):
        calls.append((url, kw))
        return SimpleNamespace(status=status, reason=reason, screenshot=None)

    run.calls = calls
    return run


def test_assisted_apply_marks_sent_on_yes(tmp_path, http):
    s, cfg = make(tmp_path, LEVER)
    run = runner("ready")
    out = assisted_apply(s, s.get(Application, 1), cfg, None, http, runner=run,
                         ask=lambda q: "y", scan=lever_scan)
    app = s.get(Application, 1)
    assert out == "Marked #1 as sent." and app.status == "sent" and app.channel == "assisted"
    assert run.calls[0][1]["submit"] is False and run.calls[0][1]["headless"] is False


def test_assisted_apply_no_leaves_status(tmp_path, http):
    s, cfg = make(tmp_path, LEVER)
    out = assisted_apply(s, s.get(Application, 1), cfg, None, http, runner=runner("ready"),
                         ask=lambda q: "", scan=lever_scan)
    assert out == 'Not marked as sent. Send "sent 1" once it\'s out.'
    assert s.get(Application, 1).status == "approved"


def test_assisted_apply_stopped_gives_link(tmp_path, http):
    s, cfg = make(tmp_path, LEVER)
    out = assisted_apply(s, s.get(Application, 1), cfg, None, http,
                         runner=runner("stopped", "login"), scan=lever_scan)
    assert out == f"Couldn't prepare the form: login. Apply here: {LEVER}"


def test_assisted_apply_reuses_saved_plan(tmp_path, http):
    s, cfg = make(tmp_path, LEVER)
    app = s.get(Application, 1)
    app.form_plan = FormPlan().to_json()
    run = runner("ready")
    assisted_apply(s, app, cfg, None, http, runner=run, ask=lambda q: "n", scan=lambda u: None)
    assert run.calls



def test_assisted_apply_does_not_replan_a_reviewed_plan(tmp_path, http, monkeypatch):
    import jobhunt.apply.prepare as prep

    s, cfg = make(tmp_path, LEVER)
    app = s.get(Application, 1)
    save_plan(s, app, FormPlan(filled=[Planned("email", "reviewed@example.com", "profile")]))
    monkeypatch.setattr(prep, "prepare_plan", lambda *a, **k: pytest.fail("re-planned"))
    run = runner("ready")
    assisted_apply(s, app, cfg, None, http, runner=run, ask=lambda q: "", scan=lever_scan)
    assert run.calls  # opened the form with the stored plan; prepare_plan would have failed


def test_unknown_country_plans_with_eu_job_none(tmp_path, http, monkeypatch):
    import jobhunt.apply.prepare as prep

    seen = {}

    def spy(fields, **kw):
        seen.update(kw)
        return FormPlan()

    monkeypatch.setattr(prep, "plan_form", spy)
    s, cfg = make(tmp_path, LEVER)
    prepare_plan(s, s.get(Application, 1), cfg, None, http, scan=lever_scan)
    assert seen["eu_job"] is None


def test_assisted_apply_refuses_after_a_maybe_sent_autopilot_run(tmp_path, http):
    from jobhunt.apply.tracker import set_status

    s, cfg = make(tmp_path, LEVER)
    app = s.get(Application, 1)
    set_status(s, app, "queued", note="autopilot error: Error - it may have gone through; check before applying again")
    run = runner("ready")
    out = assisted_apply(s, app, cfg, None, http, runner=run, ask=lambda q: "y", scan=lever_scan)
    assert not run.calls
    assert out.startswith("#1: autopilot may already have sent this (autopilot error: Error")
    assert out.endswith('send "sent 1" if it went out.')


def test_assisted_apply_refuses_a_row_left_mid_autopilot_submit(tmp_path, http):
    from jobhunt.apply.tracker import set_status

    s, cfg = make(tmp_path, LEVER)
    app = s.get(Application, 1)
    app.channel = "autopilot"
    set_status(s, app, "approved", note="autopilot: submitting")
    run = runner("ready")
    out = assisted_apply(s, app, cfg, None, http, runner=run, ask=lambda q: "y", scan=lever_scan)
    assert not run.calls
    assert "autopilot may already have sent this" in out
