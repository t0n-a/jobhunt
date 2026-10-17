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
        "Approved, apply when ready: 0\n"
        "Follow-ups due: 1\n"
        "  #3 Dev3 @ Co3: "
        + _followup_text(session)
        + "  (done 3)\n"
        "Funnel: sent 2, replied 0, interview 0, offer 0\n"
        "Trust: 0/10 clean approvals\n"
        "Autopilot: off, 0/10 today"
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


def test_approved_not_yet_sent_are_listed(session):
    add(session, 1, "approved")
    text = digest(session)
    assert "Approved, apply when ready: 1" in text
    assert "#1 Dev1 @ Co1: sent 1 once it's out" in text
    assert text.index("Waiting on you") < text.index("Approved, apply when ready")


def test_autopilot_line_and_suffix(session):
    from jobhunt.state import set_state

    add(session, 1, "sent", NOW - timedelta(hours=2)).channel = "autopilot"
    session.commit()
    set_state(session, "autopilot", "1")
    out = digest(session)
    assert "  #1 Dev1 @ Co1 (autopilot)" in out
    assert out.endswith("Autopilot: on, 1/10 today")


def test_blocked_hosts_listed_when_autopilot_on(session):
    from jobhunt.state import block_host, set_state

    block_host(session, "jobs.lever.co", "no confirmation")
    assert "Autopilot blocked" not in digest(session)
    set_state(session, "autopilot", "1")
    assert "Autopilot blocked: jobs.lever.co (no confirmation)" in digest(session)


def test_digest_outreach_section(session):
    from jobhunt.outreach.models import Outreach

    add(session, 1)
    session.add(Job(id=2, title="Dev2", company="Co2", description="d", url="u", content_hash="2"))
    session.add(Outreach(id=1, job_id=1, kind="message", text="hi"))
    session.add(Outreach(id=2, job_id=2, kind="message", text="hi", status="sent", sent_at=NOW - timedelta(days=8)))
    session.add(Outreach(id=3, job_id=2, kind="message", text="hi", status="replied"))
    session.commit()
    out = digest(session)
    assert (
        "Follow-ups due: 0\n"
        "Outreach: 1 to send, 2 sent, 1 replied\n"
        "  #1 Co1: osent 1 / oskip 1\n"
        "  Follow-up due: #2 Co2\n"
        "Funnel:"
    ) in out


def test_digest_sent_counts_distinct_contacts(session):
    from jobhunt.outreach.models import Contact, Outreach

    add(session, 1)
    session.add(Contact(id=1, company="Co1", source="posting"))
    session.add(Outreach(job_id=1, contact_id=1, kind="message", text="a", status="sent"))
    session.add(Outreach(job_id=1, contact_id=1, kind="connection_note", text="b", status="sent"))
    session.add(Outreach(job_id=1, contact_id=1, kind="followup", text="c", status="sent"))
    session.commit()
    assert "Outreach: 0 to send, 1 sent, 0 replied" in digest(session)


def test_profile_section_only_when_suggestions_wait(session):
    from jobhunt.growth.models import upsert_suggestion

    args = {"now": NOW, "scan_line": "ok", "linkedin_skipped": False, "ghosted": [], "config": CONFIG}
    assert "Profile:" not in build_digest(session, **args)
    for n in range(7):
        upsert_suggestion(session, "pin", f"ada/r{n}", title=f"Pin r{n}")
    out = build_digest(session, **args).split("\n")
    i = out.index("Profile: 7 suggestions waiting")
    assert out[i + 1] == "  #1 Pin r0" and out[i + 5].startswith("  #5") and "#6" not in out[i + 6]
