import pytest
from sqlmodel import Session

from jobhunt.apply.tracker import events_for, funnel, set_status
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job
from jobhunt.tailor.models import Application


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        s.add(Job(id=1, title="Dev", company="A", description="d", url="u", content_hash="h"))
        s.commit()
        yield s


def make_app(session, status="drafted"):
    application = Application(job_id=1, status=status)
    session.add(application)
    session.commit()
    session.refresh(application)
    return application


def test_set_status_logs_an_event_and_stamps_sent(session):
    application = make_app(session)
    set_status(session, application, "queued")
    set_status(session, application, "sent")
    assert [e.kind for e in events_for(session, application.id)] == ["queued", "sent"]
    assert application.status == "sent"
    assert application.applied_at is not None


def test_unknown_status_is_refused(session):
    with pytest.raises(ValueError):
        set_status(session, make_app(session), "applied")


def test_funnel_counts_later_stages_in_earlier_ones(session):
    a = make_app(session)
    b = make_app(session)
    set_status(session, a, "sent")
    set_status(session, a, "replied")
    set_status(session, a, "rejected")
    set_status(session, b, "sent")
    assert funnel(session) == {"sent": 2, "replied": 1, "interview": 0, "offer": 0}


def test_init_db_renames_applied_to_sent(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        s.add(Job(id=1, title="Dev", company="A", description="d", url="u", content_hash="h"))
        s.add(Application(job_id=1, status="applied"))
        s.commit()
    init_db(engine)
    with Session(engine) as s:
        assert s.get(Application, 1).status == "sent"
