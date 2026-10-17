import pytest
from sqlmodel import Session, select

from jobhunt.apply import queue
from jobhunt.apply.queue import (
    approve,
    autopilot_available,
    edit_count,
    tailor_shortlist,
    trust_count,
)
from jobhunt.config import AutopilotConfig, Config, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, JobFacts, Score
from jobhunt.llm import FakeProvider, LLMError
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore
from jobhunt.tailor.models import Application


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "q.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def no_render(monkeypatch):
    monkeypatch.setattr(queue, "render_application", lambda session, application, **kw: application)


def cfg(tmp_path, **autopilot) -> Config:
    return Config(
        profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"),
        data_dir=tmp_path,
        autopilot=AutopilotConfig(**autopilot),
    )


def add(session, title, pay, company="Acme"):
    job = Job(
        title=title,
        company=company,
        description="We need Python and Redis.",
        url="u",
        content_hash=title + company,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    session.add(
        Score(
            job_id=job.id,
            fit_score=70,
            seniority_fit="ok",
            track="backend",
            rationale="r",
            content_hash=job.content_hash,
        )
    )
    session.add(
        JobFacts(
            job_id=job.id,
            content_hash=job.content_hash,
            level="junior",
            visa="easy",
            pay_yearly_eur=pay,
        )
    )
    session.commit()
    return job


def seed_atom(session):
    return AtomStore(session).add(
        EvidenceAtom(
            kind=AtomKind.PROJECT,
            title="URL shortener",
            description="Built a link shortener in Flask, cached in Redis.",
            skills=["Python", "Flask", "Redis"],
            provenance=Provenance.INTERVIEW,
            confirmed=True,
        )
    )


def responses(atom_id, n=1, bullets=None):
    out = []
    for _ in range(n):
        resume = {
            "headline": "Backend Engineer",
            "summary": "",
            "sections": [
                {
                    "name": "Projects",
                    "entries": [
                        {
                            "atom_id": atom_id,
                            "title": "URL shortener",
                            "org": "Personal",
                            "dates": "2026",
                            "bullets": bullets
                            or [{"atom_id": atom_id, "text": "Cached responses in Redis."}],
                        }
                    ],
                }
            ],
            "skills": ["Python", "Redis"],
        }
        out += [{"keywords": ["Python", "Redis"]}, resume, {"cover_letter": "Dear team."}]
    return out


def apps(session):
    return session.exec(select(Application).order_by(Application.id)).all()


def three(session):
    add(session, "Alpha Engineer", 90_000, "Alpha")
    add(session, "Beta Engineer", 80_000, "Beta")
    add(session, "Gamma Engineer", 70_000, "Gamma")


def test_tailors_the_top_of_the_shortlist_and_queues_it(session, tmp_path):
    atom = seed_atom(session)
    three(session)

    run = tailor_shortlist(
        session, FakeProvider(responses(atom.id, 3)), cfg(tmp_path, tailor_per_run=2)
    )

    assert len(run.queued) == 2
    found = apps(session)
    assert [a.status for a in found] == ["queued", "queued"]
    assert [session.get(Job, a.job_id).company for a in found] == ["Alpha", "Beta"]
    assert all(a.bullets_at_draft == 1 for a in found)


def test_skips_jobs_that_already_have_an_application(session, tmp_path):
    atom = seed_atom(session)
    three(session)
    tailor_shortlist(session, FakeProvider(responses(atom.id, 3)), cfg(tmp_path, tailor_per_run=2))

    run = tailor_shortlist(session, FakeProvider(responses(atom.id, 3)), cfg(tmp_path))

    assert [session.get(Job, a.job_id).company for a in run.queued] == ["Gamma"]


def test_never_twice_across_duplicate_jobs(session, tmp_path):
    atom = seed_atom(session)
    add(session, "Backend Engineer", 90_000)
    add(session, "Backend Developer", 80_000)

    run = tailor_shortlist(session, FakeProvider(responses(atom.id, 2)), cfg(tmp_path))

    assert len(run.queued) == 1
    assert len(apps(session)) == 1


def test_render_failure_still_queues(session, tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("no latex")

    monkeypatch.setattr(queue, "render_application", boom)
    atom = seed_atom(session)
    add(session, "Backend Engineer", 90_000)

    run = tailor_shortlist(session, FakeProvider(responses(atom.id)), cfg(tmp_path))

    assert run.queued[0].status == "queued"
    assert run.queued[0].pdf_path is None


def test_llm_error_counts_and_continues(session, tmp_path):
    atom = seed_atom(session)
    three(session)

    class FailsOnce(FakeProvider):
        failed = False

        def complete(self, prompt, schema=None):
            if not self.failed:
                self.failed = True
                raise LLMError("down")
            return super().complete(prompt, schema)

    run = tailor_shortlist(session, FailsOnce(responses(atom.id)), cfg(tmp_path, tailor_per_run=1))

    assert run.failed == 1
    assert [session.get(Job, a.job_id).company for a in run.queued] == ["Beta"]


def test_empty_profile_stops_the_run(session, tmp_path):
    add(session, "Backend Engineer", 90_000)

    run = tailor_shortlist(session, FakeProvider([]), cfg(tmp_path))

    assert run.queued == []
    assert "confirmed evidence" in run.stopped


def queued_app(session, tmp_path, atom, bullets=None):
    add(session, "Backend Engineer", 90_000, company=f"Co{len(apps(session))}")
    c = cfg(tmp_path)
    run = tailor_shortlist(session, FakeProvider(responses(atom.id, 1, bullets)), c)
    return run.queued[0]


def test_clean_approval_earns_trust(session, tmp_path):
    application = queued_app(session, tmp_path, seed_atom(session))

    assert approve(session, application, AutopilotConfig()) is True
    assert trust_count(session) == 1


def test_heavy_edits_earn_none(session, tmp_path):
    atom = seed_atom(session)
    bullets = [{"atom_id": atom.id, "text": "Cached responses in Redis."}] * 3
    application = queued_app(session, tmp_path, atom, bullets)
    assert application.bullets_at_draft == 3
    application.resume_json = {"sections": []}
    session.add(application)
    session.commit()
    assert edit_count(application) == 3

    assert approve(session, application, AutopilotConfig(heavy_edit_limit=2)) is False
    assert trust_count(session) == 0


def test_approve_twice_is_refused(session, tmp_path):
    application = queued_app(session, tmp_path, seed_atom(session))
    approve(session, application, AutopilotConfig())

    with pytest.raises(ValueError):
        approve(session, application, AutopilotConfig())
    assert trust_count(session) == 1


def test_autopilot_available_at_target(session, tmp_path):
    atom = seed_atom(session)
    pilot = AutopilotConfig(trust_target=2)
    for _ in range(2):
        assert not autopilot_available(session, pilot) or trust_count(session) == 2
        approve(session, queued_app(session, tmp_path, atom), pilot)

    assert autopilot_available(session, pilot)
