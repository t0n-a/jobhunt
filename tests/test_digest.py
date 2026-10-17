from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from jobhunt.apply.digest import build_digest
from jobhunt.apply.tracker import set_status
from jobhunt.config import Config, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job
from jobhunt.state import set_paused
from jobhunt.tailor.models import Application

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
CONFIG = Config(profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"))


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def add(session, n, status="drafted", at=NOW):
    session.add(Job(id=n, title=f"Dev{n}", company=f"Co{n}", description="d", url="u", content_hash=str(n)))
    application = Application(id=n, job_id=n)
    session.add(application)
    session.commit()
    if status != "drafted":
        set_status(session, application, status, now=at)
    return application


def digest(session, scan_line="fetched=1 jobs=1", skipped=False, ghosted=()):
    return build_digest(
        session,
        now=NOW,
        scan_line=scan_line,
        linkedin_skipped=skipped,
        ghosted=list(ghosted),
        config=CONFIG,
    )


def test_full_digest_layout(session):
    add(session, 1, "sent", NOW - timedelta(hours=2))
    add(session, 2, "queued")
    add(session, 3, "sent", NOW - timedelta(days=8))
    assert digest(session, "fetched=1 jobs=1\n  extra") == (
        "jobhunt daily, 2026-10-17\n"
        "Scan: fetched=1 jobs=1\n"
        "Sent in the last day: 1\n"
        "  #1 Dev1 @ Co1\n"
        "Waiting on you: 1\n"
        "  #2 Dev2 @ Co2: yes 2 / no 2\n"
        "Follow-ups due: 1\n"
        "  #3 Dev3 @ Co3: "
        + _followup_text(session)
        + "  (done 3)\n"
        "Funnel: sent 2, replied 0, interview 0, offer 0\n"
        "Trust: 0/10 clean approvals"
    )


def _followup_text(session):
    from jobhunt.apply.followup import followups_due

    [f] = followups_due(session, NOW)
    return f.text


def test_empty_sections_show_zero_counts(session):
    out = digest(session)
    assert "Waiting on you: 0" in out
    assert "#" not in out


def test_paused_and_linkedin_lines(session):
    set_paused(session, True)
    lines = digest(session, skipped=True).split("\n")
    assert lines[1].startswith("Paused")
    assert "LinkedIn was skipped this run (blocked or slow)." in lines


def test_no_guilt_words(session):
    for n in range(1, 4):
        add(session, n, "queued")
    for out in (digest(session), digest(session)):
        assert not any(w in out.casefold() for w in ("missed", "streak", "behind", "should have"))


def test_lists_cap_at_ten(session):
    for n in range(1, 16):
        add(session, n, "queued")
    out = digest(session)
    assert "Waiting on you: 15" in out
    assert sum(line.startswith("  #") for line in out.split("\n")) == 10
