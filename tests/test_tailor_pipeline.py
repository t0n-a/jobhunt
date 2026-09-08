import pytest
from sqlmodel import Session

from jobhunt.config import ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, posting_hash
from jobhunt.llm import FakeProvider
from jobhunt.matching.score import EmptyProfileError
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore
from jobhunt.tailor.pipeline import mark_applied, tailor_job


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def profile() -> ProfileConfig:
    return ProfileConfig(name="Ada", email="ada@example.com", location="Porto")


def seed_job(session) -> Job:
    description = "We need Python and Redis."
    job = Job(
        title="Backend Engineer",
        company="Acme",
        description=description,
        url="u",
        content_hash=posting_hash("Backend Engineer", "Acme", description),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def seed_atom(session, *, confirmed=True) -> EvidenceAtom:
    return AtomStore(session).add(
        EvidenceAtom(
            kind=AtomKind.PROJECT,
            title="URL shortener",
            description="Built a link shortener in Flask, cached in Redis.",
            skills=["Python", "Flask", "Redis"],
            provenance=Provenance.INTERVIEW,
            confirmed=confirmed,
        )
    )


def responses(atom_id, *, bullets=None):
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
    return [
        {"keywords": ["Python", "Redis"]},
        resume,
        {"cover_letter": "Dear Acme team, I built a link shortener."},
    ]


def test_tailoring_produces_an_application(session):
    job = seed_job(session)
    atom = seed_atom(session)

    application = tailor_job(session, job, FakeProvider(responses(atom.id)), profile=profile())

    assert application.id is not None
    assert application.job_id == job.id
    assert application.status == "drafted"
    assert application.keywords == ["Python", "Redis"]
    assert application.cover_letter.startswith("Dear Acme team")


def test_a_fabricated_bullet_is_dropped_and_reported(session):
    """End to end: the model invents a metric, and it does not reach the
    document the user is about to send."""
    job = seed_job(session)
    atom = seed_atom(session)
    bullets = [
        {"atom_id": atom.id, "text": "Cached responses in Redis."},
        {"atom_id": atom.id, "text": "Cut latency by 40%."},
    ]

    application = tailor_job(
        session, job, FakeProvider(responses(atom.id, bullets=bullets)), profile=profile()
    )

    kept = [
        b["text"]
        for section in application.resume_json["sections"]
        for entry in section["entries"]
        for b in entry["bullets"]
    ]
    assert kept == ["Cached responses in Redis."]
    assert len(application.dropped_bullets) == 1
    assert "40" in application.dropped_bullets[0]["reason"]


def test_the_cover_letter_only_sees_verified_bullets(session):
    """Written after the verifier, so it cannot cite a claim the resume dropped."""
    job = seed_job(session)
    atom = seed_atom(session)
    bullets = [
        {"atom_id": atom.id, "text": "Cached responses in Redis."},
        {"atom_id": atom.id, "text": "Cut latency by 40%."},
    ]
    llm = FakeProvider(responses(atom.id, bullets=bullets))

    tailor_job(session, job, llm, profile=profile())

    letter_prompt = llm.prompts[-1]
    assert "Cached responses in Redis." in letter_prompt
    assert "40%" not in letter_prompt


def test_an_unconfirmed_atom_never_reaches_the_document(session):
    job = seed_job(session)
    atom = seed_atom(session, confirmed=False)

    with pytest.raises(EmptyProfileError):
        tailor_job(session, job, FakeProvider(responses(atom.id)), profile=profile())


def test_tailoring_with_no_atoms_is_refused(session):
    """A resume built with no evidence would be entirely the model's invention."""
    job = seed_job(session)

    with pytest.raises(EmptyProfileError):
        tailor_job(session, job, FakeProvider(responses(1)), profile=profile())


def test_a_fresh_application_has_not_been_rendered(session):
    job = seed_job(session)
    atom = seed_atom(session)

    application = tailor_job(session, job, FakeProvider(responses(atom.id)), profile=profile())

    assert application.pdf_path is None
    assert application.coverage is None


def test_mark_applied_records_when(session):
    job = seed_job(session)
    atom = seed_atom(session)
    application = tailor_job(session, job, FakeProvider(responses(atom.id)), profile=profile())

    updated = mark_applied(session, application)

    assert updated.status == "applied"
    assert updated.applied_at is not None
