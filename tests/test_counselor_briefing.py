from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.counselor import tools
from jobhunt.counselor.briefing import build_briefing, render_briefing
from jobhunt.counselor.models import Conversation, CounselorNote
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, Score
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance


@pytest.fixture
def config(tmp_path):
    return Config(
        profile=ProfileConfig(
            name="Ada", email="a@example.com", location="Porto", github_username="ada"
        ),
        llm=LLMConfig(cache_dir=tmp_path / "cache"),
        data_dir=tmp_path,
    )


@pytest.fixture
def db(tmp_path):
    engine = create_engine_for(tmp_path / "db.sqlite")
    init_db(engine)
    with Session(engine) as session:
        yield session


def seed(session):
    session.add(
        EvidenceAtom(
            kind=AtomKind.PROJECT,
            title="URL shortener",
            description="Flask.",
            skills=["Python", "Redis"],
            metrics="2k req/s",
            provenance=Provenance.GITHUB,
            confirmed=True,
            source_ref="https://github.com/ada/shortener",
        )
    )
    session.add(
        EvidenceAtom(
            kind=AtomKind.COURSE,
            title="Distributed Systems",
            description="Raft.",
            skills=["Raft"],
            provenance=Provenance.COURSEWORK,
            confirmed=False,
        )
    )
    for i in range(3):
        job = Job(
            title=f"Backend {i}", company="Acme", description="d", url="", content_hash=f"h{i}"
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        if i < 2:
            session.add(
                Score(
                    job_id=job.id,
                    fit_score=70,
                    matched_skills=[],
                    missing_skills=["Docker"],
                    red_flags=[],
                    seniority_fit="match",
                    track="backend",
                    rationale="r",
                    model="fake",
                    content_hash=f"h{i}",
                )
            )
    session.commit()


def test_first_conversation_with_empty_store(db, config, monkeypatch):
    monkeypatch.setattr(tools, "fetch_repos", lambda u, token=None: [])
    b = build_briefing(db, config=config, conversation=None)
    assert b.first_conversation and b.days_since is None
    assert (b.confirmed, b.unconfirmed, b.jobs_total) == (0, 0, 0)
    text = render_briefing(b)
    assert "first conversation" in text and "No evidence atoms yet" in text


def test_briefing_counts_thin_atoms_gaps_and_uncovered_repos(db, config, monkeypatch):
    seed(db)
    monkeypatch.setattr(
        tools,
        "fetch_repos",
        lambda u, token=None: [
            {
                "name": "shortener",
                "full_name": "ada/shortener",
                "html_url": "https://github.com/ada/shortener",
                "language": "Python",
                "pushed_at": "",
                "readme": "",
            },
            {
                "name": "thesis",
                "full_name": "ada/thesis",
                "html_url": "https://github.com/ada/thesis",
                "language": "Go",
                "pushed_at": "",
                "readme": "",
            },
        ],
    )
    conv = Conversation(last_turn_at=datetime.now(UTC) - timedelta(days=9))
    db.add(conv)
    db.commit()
    db.refresh(conv)
    db.add(CounselorNote(conversation_id=conv.id, kind="goal", text="wants ML"))
    db.add(CounselorNote(conversation_id=conv.id, kind="thread", text="ask about the internship"))
    db.add(
        CounselorNote(conversation_id=conv.id, kind="thread", text="closed one", status="closed")
    )
    db.commit()

    b = build_briefing(db, config=config, conversation=conv)
    assert not b.first_conversation and b.days_since == 9
    assert (b.confirmed, b.unconfirmed) == (1, 1)
    assert b.thin == ("Distributed Systems",)
    assert (b.jobs_total, b.jobs_scored) == (3, 2)
    assert b.gaps[0].startswith("Docker — missing from 2 jobs")
    assert b.repos_uncovered == ("ada/thesis (Go)",) and b.repos_note == ""
    assert b.notes == {"goal": ("wants ML",), "thread": ("ask about the internship",)}

    text = render_briefing(b)
    assert "9 days" in text and "## Open threads" in text and "ask about the internship" in text
    assert "closed one" not in text


def test_repo_failure_becomes_a_note_not_an_error(db, config, monkeypatch):
    def boom(u, token=None):
        raise RuntimeError("offline")

    monkeypatch.setattr(tools, "fetch_repos", boom)
    b = build_briefing(db, config=config, conversation=None)
    assert b.repos_uncovered == () and "offline" in b.repos_note
    assert "offline" in render_briefing(b)
