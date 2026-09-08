import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, Score, posting_hash
from jobhunt.llm import FakeProvider
from jobhunt.web.app import create_app
from jobhunt.web.deps import get_llm

QUEST = {
    "title": "Deploy to k3s",
    "deliverable": "A public repo",
    "target_bullet": "Deployed a Python service to k3s with manifests.",
    "steps": [
        {
            "title": "Stand up k3s",
            "instructions": "Install it.",
            "verification": {"type": "manual"},
        },
        {
            "title": "Write manifests",
            "instructions": "Add deploy.yaml.",
            "verification": {"type": "manual"},
        },
    ],
}


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


def seed_gap(client, skill="Kubernetes", fit=85):
    with Session(client.engine) as session:
        job = Job(
            title="Platform Engineer",
            company="Acme",
            description="d",
            url="u",
            content_hash=posting_hash("Platform Engineer", "Acme", "d"),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        session.add(
            Score(
                job_id=job.id,
                fit_score=fit,
                matched_skills=[],
                missing_skills=[skill],
                red_flags=[],
                seniority_fit="under",
                track="devops",
                rationale="r",
                content_hash=job.content_hash,
            )
        )
        session.commit()


def test_today_with_no_quest_offers_to_start_one(make_client):
    client = make_client([])

    text = client.get("/today").text

    assert "no active quest" in text.casefold()


def test_gaps_page_ranks_skills(make_client):
    client = make_client([])
    seed_gap(client)

    text = client.get("/gaps").text

    assert "Kubernetes" in text


def test_starting_a_quest_from_a_gap_shows_the_first_task(make_client):
    client = make_client([QUEST])
    seed_gap(client)

    client.post("/gaps/Kubernetes/start", follow_redirects=True)
    text = client.get("/today").text

    assert "Stand up k3s" in text


def test_today_shows_the_reward_before_the_work(make_client):
    client = make_client([QUEST])
    seed_gap(client)
    client.post("/gaps/Kubernetes/start", follow_redirects=True)

    text = client.get("/today").text

    assert "Deployed a Python service" in text


def test_today_never_shows_the_next_step(make_client):
    """One task visible. A backlog is what makes the next task a choice."""
    client = make_client([QUEST])
    seed_gap(client)
    client.post("/gaps/Kubernetes/start", follow_redirects=True)

    text = client.get("/today").text

    assert "Stand up k3s" in text
    assert "Write manifests" not in text


def test_done_advances_and_earns_an_atom(make_client):
    client = make_client([QUEST])
    seed_gap(client)
    client.post("/gaps/Kubernetes/start", follow_redirects=True)

    client.post("/today/done", data={"proof_note": "Installed k3s."}, follow_redirects=True)
    text = client.get("/today").text

    assert "Write manifests" in text
    assert "Stand up k3s" not in text


def test_swap_moves_on_without_ending_the_quest(make_client):
    client = make_client([QUEST])
    seed_gap(client)
    client.post("/gaps/Kubernetes/start", follow_redirects=True)

    client.post("/today/swap", follow_redirects=True)
    text = client.get("/today").text

    assert "Write manifests" in text


def test_no_streak_language_anywhere_on_the_page(make_client):
    """A broken-streak screen is what converts a lapse into abandonment."""
    client = make_client([QUEST])
    seed_gap(client)
    client.post("/gaps/Kubernetes/start", follow_redirects=True)

    text = client.get("/today").text.casefold()

    assert "streak" not in text
    assert "you missed" not in text
    assert "don't break" not in text
