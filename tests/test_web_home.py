import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jobhunt.coach.models import Quest, QuestStep
from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, Score
from jobhunt.llm import FakeProvider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.web.app import create_app
from jobhunt.web.deps import get_llm
from jobhunt.web.home import home_state, next_step


@pytest.fixture
def client(tmp_path):
    cfg = Config(
        profile=ProfileConfig(name="Ada", email="ada@example.com", location="Porto"),
        llm=LLMConfig(cache_dir=tmp_path / "cache"),
        data_dir=tmp_path,
    )
    app = create_app(cfg)
    app.dependency_overrides[get_llm] = lambda: FakeProvider([])
    with TestClient(app) as c:
        c.engine = create_engine_for(tmp_path / "jobhunt.db")
        init_db(c.engine)
        yield c


def atom(confirmed: bool) -> EvidenceAtom:
    return EvidenceAtom(
        kind=AtomKind.PROJECT,
        title="T",
        description="D",
        skills=["Python"],
        provenance=Provenance.CV,
        confirmed=confirmed,
    )


def test_next_step_walks_the_pipeline_in_order():
    assert next_step(0, 0, 0, None).href == "/import"
    assert next_step(1, 2, 0, None) == next_step(0, 2, 0, None)
    assert next_step(1, 2, 0, None).text == "Confirm 2 atoms so they can be used."
    assert next_step(1, 0, 0, None).href is None
    assert "jobhunt scan" in next_step(1, 0, 0, None).text
    assert next_step(1, 0, 5, "Ship a Dockerfile").text == "Today: Ship a Dockerfile"
    assert next_step(1, 0, 5, "Ship a Dockerfile").href == "/today"
    assert next_step(1, 0, 5, None).href == "/gaps"


def test_home_state_counts_from_the_database(client):
    with Session(client.engine) as db:
        db.add(atom(True))
        db.add(atom(True))
        db.add(atom(False))
        job = Job(
            title="Backend Engineer",
            company="Acme",
            description="x",
            url="",
            content_hash="h1",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        db.add(
            Score(
                job_id=job.id,
                fit_score=80,
                matched_skills=[],
                missing_skills=[],
                red_flags=[],
                seniority_fit="match",
                track="backend",
                rationale="r",
                model="fake",
                content_hash="h1",
            )
        )
        quest = Quest(
            skill="Docker",
            gap_job_count=3,
            title="Docker basics",
            deliverable="d",
            target_bullet="b",
            status="active",
        )
        db.add(quest)
        db.commit()
        db.refresh(quest)
        db.add(
            QuestStep(
                quest_id=quest.id,
                ordinal=1,
                title="Ship a Dockerfile",
                instructions="i",
                status="pending",
            )
        )
        db.commit()
        state = home_state(db)

    assert (state.confirmed, state.unconfirmed) == (2, 1)
    assert (state.jobs_scored, state.jobs_total) == (1, 1)
    assert state.today_title == "Ship a Dockerfile"
    assert state.next_step.text == "Confirm 1 atoms so they can be used."


def test_root_renders_home_with_the_banner(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Start by putting something in your profile." in response.text
    assert 'aria-current="page">Home' in response.text
