from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from jobhunt.apply.followup import followups_due, mark_cold, mark_followup_done
from jobhunt.apply.tracker import set_status
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job
from jobhunt.tailor.models import Application

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def days(n):
    return T0 + timedelta(days=n)


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        s.add(Job(id=1, title="Dev", company="A", description="d", url="u", content_hash="h"))
        s.commit()
        yield s


def make_app(session, status="drafted", at=T0):
    application = Application(job_id=1, status="drafted")
    session.add(application)
    session.commit()
    session.refresh(application)
    if status != "drafted":
        set_status(session, application, status, now=at)
    return application


def test_sent_is_due_after_seven_days(session):
    app = make_app(session, "sent")
    assert followups_due(session, days(6)) == []
    [due] = followups_due(session, days(7))
    assert due.application_id == app.id
    assert due.kind == "nudge"
    assert due.due_at == days(7)
    assert "(nudge 1/2)" in due.text


def test_second_nudge_counts_from_the_first(session):
    app = make_app(session, "sent")
    mark_followup_done(session, app, now=days(8))
    assert followups_due(session, days(14)) == []
    [due] = followups_due(session, days(15))
    assert due.kind == "nudge"
    assert "(nudge 2/2)" in due.text


def test_after_two_nudges_it_goes_cold(session):
    app = make_app(session, "sent")
    mark_followup_done(session, app, now=days(7))
    mark_followup_done(session, app, now=days(14))
    assert followups_due(session, days(21)) == []
    assert mark_cold(session, days(21)) == [app]
    assert app.status == "ghosted"
    assert mark_cold(session, days(21)) == []


def test_replied_is_due_after_one_then_three_days(session):
    app = make_app(session, "replied")
    [due] = followups_due(session, days(1))
    assert due.kind == "reply"
    assert due.text == "Reply to keep the conversation going."
    mark_followup_done(session, app, now=days(1))
    assert followups_due(session, days(3)) == []
    [due] = followups_due(session, days(4))
    assert due.due_at == days(4)
    mark_followup_done(session, app, now=days(4))
    assert followups_due(session, days(30)) == []


def test_interview_thanks_once(session):
    app = make_app(session, "interview")
    [due] = followups_due(session, days(1))
    assert due.kind == "thanks"
    assert due.text == "Send a thank-you note after your interview."
    mark_followup_done(session, app, now=days(1))
    assert followups_due(session, days(30)) == []


def test_new_status_resets_the_count(session):
    app = make_app(session, "sent")
    mark_followup_done(session, app, now=days(7))
    mark_followup_done(session, app, now=days(14))
    set_status(session, app, "replied", now=days(20))
    [due] = followups_due(session, days(21))
    assert due.kind == "reply"
    assert due.due_at == days(21)


def test_other_statuses_have_no_followups(session):
    for status in ("queued", "skipped", "rejected"):
        make_app(session, status)
    assert followups_due(session, days(365)) == []
