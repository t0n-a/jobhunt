from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from jobhunt.config import Config, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, JobFacts, Score
from jobhunt.outreach import log as outlog
from jobhunt.outreach.draft import Draft, DraftResult
from jobhunt.outreach.log import (
    candidates,
    draft_followups,
    draft_for_run,
    followups_due,
    mark,
)
from jobhunt.outreach.models import Outreach
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore
from jobhunt.state import set_paused
from jobhunt.tailor.models import Application

NOW = datetime(2026, 10, 17, tzinfo=UTC)


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "o.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def fake_drafts(monkeypatch):
    def fake(job, contact, atoms, llm, *, profile):
        return DraftResult(drafts=[Draft("message", f"Hi about {job.title}", [atoms[0].id])])

    monkeypatch.setattr(outlog, "draft_outreach", fake)
    monkeypatch.setattr(
        outlog, "draft_followup", lambda o, j, c, a, llm: Draft("followup", "Following up", [a[0].id])
    )


def cfg(tmp_path) -> Config:
    return Config(
        profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"), data_dir=tmp_path
    )


def add(session, title, pay, company="Acme", fit=90, recruiter="Ann Lee"):
    job = Job(
        title=title,
        company=company,
        description=f"Python. Reach out: {recruiter}, Technical Recruiter.",
        url="u",
        content_hash=title + company,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    session.add(
        Score(job_id=job.id, fit_score=fit, seniority_fit="ok", track="b", rationale="r", content_hash=job.content_hash)
    )
    session.add(JobFacts(job_id=job.id, content_hash=job.content_hash, level="junior", visa="easy", pay_yearly_eur=pay))
    session.commit()
    return job


def seed_atom(session):
    return AtomStore(session).add(
        EvidenceAtom(
            kind=AtomKind.PROJECT,
            title="URL shortener",
            description="Built a link shortener.",
            skills=["Python"],
            provenance=Provenance.INTERVIEW,
            confirmed=True,
        )
    )


def test_strong_matches_only(session, tmp_path):
    strong = add(session, "Strong", 90000, company="A", fit=85)
    add(session, "Weak", 80000, company="B", fit=60)
    rejected = add(session, "Rej", 70000, company="C", fit=95)
    session.add(Application(job_id=rejected.id, status="rejected"))
    session.commit()
    assert [j.id for j in candidates(session, cfg(tmp_path))] == [strong.id]


def test_never_twice_to_the_same_contact(session, tmp_path):
    seed_atom(session)
    add(session, "One", 90000)
    add(session, "Two", 80000)
    out = draft_for_run(session, cfg(tmp_path), None)
    assert len({o.job_id for o in out}) == 1
    assert len({o.contact_id for o in out}) == 1


def test_skipped_person_is_not_messaged_again_on_another_job(session, tmp_path):
    seed_atom(session)
    add(session, "One", 90000)
    for o in draft_for_run(session, cfg(tmp_path), None):
        mark(session, o.id, "skipped", NOW)
    add(session, "Two", 80000)  # same recruiter, same company
    assert draft_for_run(session, cfg(tmp_path), None) == []
    assert {o.status for o in session.exec(select(Outreach)).all()} == {"skipped"}


def test_one_open_outreach_per_company(session, tmp_path):
    seed_atom(session)
    add(session, "One", 90000, recruiter="Ann Lee")
    add(session, "Two", 80000, recruiter="Bob Ray")
    assert len(draft_for_run(session, cfg(tmp_path), None)) == 1
    assert candidates(session, cfg(tmp_path)) == []


def test_paused_drafts_nothing(session, tmp_path):
    seed_atom(session)
    add(session, "One", 90000)
    set_paused(session, True)
    assert draft_for_run(session, cfg(tmp_path), None) == []


def test_no_confirmed_atoms_drafts_nothing(session, tmp_path):
    add(session, "One", 90000)
    assert draft_for_run(session, cfg(tmp_path), None) == []


def test_failed_draft_is_skipped_and_retried(session, tmp_path, monkeypatch):
    seed_atom(session)
    add(session, "One", 90000)
    monkeypatch.setattr(outlog, "draft_outreach", lambda *a, **k: DraftResult())
    assert draft_for_run(session, cfg(tmp_path), None) == []
    assert len(candidates(session, cfg(tmp_path))) == 1


def test_mark_transitions(session, tmp_path):
    seed_atom(session)
    add(session, "One", 90000)
    o = draft_for_run(session, cfg(tmp_path), None)[0]
    with pytest.raises(ValueError):
        mark(session, o.id, "replied", NOW)
    assert mark(session, o.id, "sent", NOW).sent_at is not None
    with pytest.raises(ValueError):
        mark(session, o.id, "skipped", NOW)
    assert mark(session, o.id, "replied", NOW).replied_at is not None
    with pytest.raises(ValueError):
        mark(session, o.id, "sent", NOW)


def test_followup_due_after_seven_days_once(session, tmp_path):
    seed_atom(session)
    add(session, "One", 90000)
    o = draft_for_run(session, cfg(tmp_path), None)[0]
    mark(session, o.id, "sent", NOW)
    c = cfg(tmp_path)
    assert followups_due(session, NOW + timedelta(days=6), c) == []
    assert [x.id for x in followups_due(session, NOW + timedelta(days=7), c)] == [o.id]
    made = draft_followups(session, c, None, NOW + timedelta(days=7))
    assert made[0].kind == "followup" and made[0].parent_id == o.id
    assert followups_due(session, NOW + timedelta(days=30), c) == []
    mark(session, made[0].id, "sent", NOW + timedelta(days=7))
    assert followups_due(session, NOW + timedelta(days=30), c) == []


def test_replied_gets_no_followup(session, tmp_path):
    seed_atom(session)
    add(session, "One", 90000)
    o = draft_for_run(session, cfg(tmp_path), None)[0]
    mark(session, o.id, "sent", NOW)
    mark(session, o.id, "replied", NOW)
    assert followups_due(session, NOW + timedelta(days=30), cfg(tmp_path)) == []


def test_skipped_company_can_get_a_new_contact_later(session, tmp_path):
    seed_atom(session)
    add(session, "One", 90000, recruiter="Ann Lee")
    first = draft_for_run(session, cfg(tmp_path), None)
    for o in first:
        mark(session, o.id, "skipped", NOW)
    second = add(session, "Two", 80000, recruiter="Bob Ray")
    assert [j.id for j in candidates(session, cfg(tmp_path))] == [second.id]
    # the same person is still not contacted again
    third = add(session, "Three", 70000, company="Acme", recruiter="Ann Lee")
    assert third.id not in [j.id for j in candidates(session, cfg(tmp_path))]
    assert session.exec(select(Outreach)).all()


def test_lone_connection_note_is_not_stored(session, tmp_path, monkeypatch):
    seed_atom(session)
    add(session, "One", 90000)
    monkeypatch.setattr(
        outlog, "draft_outreach",
        lambda job, c, atoms, llm, *, profile: DraftResult(drafts=[Draft("connection_note", "Hi", [atoms[0].id])]),
    )
    assert draft_for_run(session, cfg(tmp_path), None) == []
    assert session.exec(select(Outreach)).all() == []
    assert len(candidates(session, cfg(tmp_path))) == 1


def test_stale_drafted_does_not_block_company(session, tmp_path):
    seed_atom(session)
    job = add(session, "One", 90000)
    old = Outreach(job_id=job.id, kind="message", text="x", created_at=datetime.now(UTC) - timedelta(days=15))
    session.add(old)
    session.commit()
    assert len(candidates(session, cfg(tmp_path))) == 1
    old.created_at = datetime.now(UTC) - timedelta(days=2)
    session.add(old)
    session.commit()
    assert candidates(session, cfg(tmp_path)) == []


def test_job_whose_draft_fails_twice_stops_being_a_candidate(session, tmp_path, monkeypatch):
    seed_atom(session)
    bad = add(session, "Bad", 90000, company="A")
    good = add(session, "Good", 80000, company="B")
    real = outlog.draft_outreach
    monkeypatch.setattr(
        outlog, "draft_outreach",
        lambda job, *a, **k: DraftResult() if job.id == bad.id else real(job, *a, **k),
    )
    c = cfg(tmp_path)
    assert [j.id for j in candidates(session, c)] == [bad.id, good.id]
    draft_for_run(session, c, None)
    assert bad.id in [j.id for j in candidates(session, c)]
    draft_for_run(session, c, None)
    assert bad.id not in [j.id for j in candidates(session, c)]


def test_llm_outage_does_not_count_as_a_failed_attempt(session, tmp_path, monkeypatch):
    seed_atom(session)
    job = add(session, "One", 90000)
    down = DraftResult(dropped=[{"kind": "all", "text": "", "reason": "llm error"}])
    monkeypatch.setattr(outlog, "draft_outreach", lambda *a, **k: down)
    for _ in range(3):
        draft_for_run(session, cfg(tmp_path), None)
    assert [j.id for j in candidates(session, cfg(tmp_path))] == [job.id]


def test_stale_drafted_row_is_skipped_when_a_new_draft_replaces_it(session, tmp_path):
    seed_atom(session)
    job = add(session, "One", 90000)
    old = Outreach(job_id=job.id, kind="message", text="x", created_at=datetime.now(UTC) - timedelta(days=15))
    session.add(old)
    session.commit()
    assert len(draft_for_run(session, cfg(tmp_path), None)) == 1
    session.refresh(old)
    assert old.status == "skipped"
