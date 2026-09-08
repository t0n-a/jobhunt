import pytest
from sqlmodel import Session

from jobhunt.db import create_engine_for, init_db
from jobhunt.growth.gaps import compute_gaps
from jobhunt.jobs.models import Job, Score, posting_hash


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def add_job(session, title, fit, missing, *, stale=False) -> Job:
    job = Job(
        title=title,
        company="Acme",
        description="d",
        url="u",
        content_hash=posting_hash(title, "Acme", "d"),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    session.add(
        Score(
            job_id=job.id,
            fit_score=fit,
            matched_skills=[],
            missing_skills=missing,
            red_flags=[],
            seniority_fit="under",
            track="backend",
            rationale="r",
            content_hash="STALE" if stale else job.content_hash,
        )
    )
    session.commit()
    return job


def test_no_scores_yields_no_gaps(session):
    assert compute_gaps(session) == []


def test_a_gap_counts_the_jobs_that_want_it(session):
    add_job(session, "A", 80, ["Kubernetes"])
    add_job(session, "B", 70, ["Kubernetes", "Docker"])

    gaps = {g.skill: g for g in compute_gaps(session)}

    assert gaps["Kubernetes"].job_count == 2
    assert gaps["Docker"].job_count == 1


def test_gaps_are_ranked_by_fit_weight_not_raw_count(session):
    """A gap blocking one excellent match matters more than one blocking two
    poor matches the user would not get anyway."""
    add_job(session, "A", 95, ["Kubernetes"])
    add_job(session, "B", 41, ["Docker"])
    add_job(session, "C", 42, ["Docker"])

    ranked = [g.skill for g in compute_gaps(session)]

    assert ranked[0] == "Kubernetes"


def test_low_fit_jobs_are_excluded_by_default(session):
    add_job(session, "A", 10, ["Fortran"])

    assert compute_gaps(session) == []


def test_stale_scores_are_ignored(session):
    """A score whose content_hash no longer matches its job describes text the
    job no longer has, so its gaps are not real."""
    add_job(session, "A", 90, ["Kubernetes"], stale=True)

    assert compute_gaps(session) == []


def test_skill_names_are_canonicalised_before_counting(session):
    """AWS and Amazon Web Services must not split one gap into two — the split
    is what would send the user to study the second-most-common skill."""
    add_job(session, "A", 80, ["AWS"])
    add_job(session, "B", 80, ["Amazon Web Services"])

    gaps = {g.skill: g for g in compute_gaps(session)}

    assert gaps["AWS"].job_count == 2


def test_example_titles_are_captured_for_the_motivating_sentence(session):
    add_job(session, "Platform Engineer", 80, ["Kubernetes"])

    gap = compute_gaps(session)[0]

    assert "Platform Engineer" in gap.example_titles


def test_limit_caps_the_returned_gaps(session):
    for i in range(30):
        add_job(session, f"J{i}", 80, [f"Skill{i}"])

    assert len(compute_gaps(session, limit=5)) == 5
