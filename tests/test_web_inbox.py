import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, Score, posting_hash
from jobhunt.llm import FakeProvider
from jobhunt.web.app import create_app
from jobhunt.web.deps import get_llm


@pytest.fixture
def make_client(tmp_path):
    def _make(responses):
        cfg = Config(
            profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"),
            llm=LLMConfig(cache_dir=tmp_path / "cache"),
            data_dir=tmp_path,
        )
        app = create_app(cfg)
        app.dependency_overrides[get_llm] = lambda: FakeProvider(responses)
        client = TestClient(app)
        client.engine = create_engine_for(tmp_path / "jobhunt.db")
        init_db(client.engine)
        return client

    return _make


def seed(client, title="Backend Engineer", fit=80, track="backend") -> int:
    with Session(client.engine) as session:
        job = Job(
            title=title,
            company="Acme",
            description="Python and Redis.",
            url="https://example.com/1",
            content_hash=posting_hash(title, "Acme", "Python and Redis."),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        session.add(
            Score(
                job_id=job.id,
                fit_score=fit,
                matched_skills=["Python"],
                missing_skills=["Kubernetes"],
                red_flags=[],
                seniority_fit="under",
                track=track,
                rationale="Good overlap.",
                content_hash=job.content_hash,
            )
        )
        session.commit()
        return job.id


def score_payload(**over):
    payload = {
        "fit_score": 65,
        "matched_skills": ["Python"],
        "missing_skills": ["Docker"],
        "red_flags": [],
        "seniority_fit": "under",
        "track": "backend",
        "rationale": "Reasonable overlap.",
    }
    payload.update(over)
    return payload


def test_inbox_renders_empty_state(make_client):
    client = make_client([])

    response = client.get("/jobs")

    assert response.status_code == 200
    assert "No jobs yet" in response.text


def test_inbox_lists_scored_jobs_highest_first(make_client):
    client = make_client([])
    seed(client, title="Low fit", fit=30)
    seed(client, title="High fit", fit=90)

    text = client.get("/jobs").text

    assert text.index("High fit") < text.index("Low fit")


def test_inbox_shows_the_track_breakdown(make_client):
    client = make_client([])
    seed(client, title="A", track="backend")
    seed(client, title="B", track="backend")
    seed(client, title="C", track="ai")

    text = client.get("/jobs").text

    assert "backend" in text and "ai" in text


def test_job_detail_shows_missing_skills(make_client):
    client = make_client([])
    job_id = seed(client)

    text = client.get(f"/jobs/{job_id}").text

    assert "Kubernetes" in text


def test_job_detail_unknown_id_returns_404(make_client):
    client = make_client([])

    assert client.get("/jobs/99999").status_code == 404


def test_paste_creates_and_scores_a_job(make_client):
    client = make_client([score_payload()])

    response = client.post(
        "/jobs/paste",
        data={
            "title": "Platform Engineer",
            "company": "Acme",
            "url": "https://example.com/9",
            "description": "Kubernetes and Terraform.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Platform Engineer" in response.text


def test_paste_maps_llm_failure_to_502(make_client):
    client = make_client([])  # exhausted provider raises LLMError

    response = client.post(
        "/jobs/paste",
        data={
            "title": "T",
            "company": "C",
            "url": "u",
            "description": "d",
        },
    )

    assert response.status_code == 502


def test_rescore_replaces_the_existing_score(make_client):
    client = make_client([score_payload(fit_score=95)])
    job_id = seed(client, fit=10)

    client.post(f"/jobs/{job_id}/rescore", follow_redirects=True)

    with Session(client.engine) as session:
        scores = session.exec(select(Score).where(Score.job_id == job_id)).all()
    assert len(scores) == 1
    assert scores[0].fit_score == 95


def seed_unscored(client, title="Unscored role") -> int:
    """A job with no Score row — what a scan leaves behind when the LLM fails."""
    with Session(client.engine) as session:
        job = Job(
            title=title,
            company="Acme",
            description="Python and Redis.",
            url="https://example.com/2",
            content_hash=posting_hash(title, "Acme", "Python and Redis."),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


def test_unscored_jobs_still_appear_marked(make_client):
    """A failed score must stay visible and distinguishable from a job that was
    never found at all — hiding it makes the two indistinguishable."""
    client = make_client([])
    seed_unscored(client, title="Zephyr unscored role")

    text = client.get("/jobs").text

    assert "Zephyr unscored role" in text
    assert "unscored" in text


def test_unscored_jobs_sort_after_every_scored_job(make_client):
    client = make_client([])
    seed_unscored(client, title="Zephyr unscored role")
    seed(client, title="Quixote low fit", fit=1)

    text = client.get("/jobs").text

    assert text.index("Quixote low fit") < text.index("Zephyr unscored role")


def test_track_breakdown_excludes_unscored_jobs(make_client):
    client = make_client([])
    seed(client, title="Quixote scored", track="devops")
    seed_unscored(client, title="Zephyr unscored role")

    text = client.get("/jobs").text

    # One scored devops job: the breakdown must count it once and not invent a
    # track for the unscored one.
    assert "devops · 1" in text


def test_inbox_with_only_unscored_jobs_shows_no_track_breakdown(make_client):
    client = make_client([])
    seed_unscored(client, title="Zephyr unscored role")

    text = client.get("/jobs").text

    assert "Zephyr unscored role" in text
    assert "best matches look like" not in text
