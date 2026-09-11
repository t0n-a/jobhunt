import json
import time

import pytest
from sqlmodel import Session

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.counselor import tools
from jobhunt.counselor.tools import cached_repos, run_tool
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
        session.add(
            EvidenceAtom(
                kind=AtomKind.PROJECT,
                title="URL shortener",
                description="Flask and Redis.",
                skills=["Python", "Redis"],
                provenance=Provenance.CV,
                confirmed=True,
                source_ref="https://github.com/ada/shortener",
            )
        )
        job = Job(
            title="Backend Engineer",
            company="Acme",
            description="Python and Docker. " * 100,
            url="",
            content_hash="h",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        session.add(
            Score(
                job_id=job.id,
                fit_score=80,
                matched_skills=["Python"],
                missing_skills=["Docker"],
                red_flags=[],
                seniority_fit="match",
                track="backend",
                rationale="r",
                model="fake",
                content_hash="h",
            )
        )
        session.commit()
        yield session


def test_atom_tool_returns_the_full_atom(db, config):
    text = run_tool("atom", {"id": 1}, db, config=config)
    assert "URL shortener" in text and "Redis" in text and "github.com/ada/shortener" in text


def test_atom_tool_unknown_id_is_an_error_string(db, config):
    assert run_tool("atom", {"id": 99}, db, config=config).startswith("error:")


def test_atoms_with_skill_matches_canonically(db, config):
    assert "URL shortener" in run_tool("atoms_with_skill", {"skill": "python"}, db, config=config)
    assert run_tool("atoms_with_skill", {"skill": "Kubernetes"}, db, config=config) == "none"


def test_gap_tool_reports_jobs_and_whether_atoms_mention_it(db, config):
    text = run_tool("gap", {"skill": "Docker"}, db, config=config)
    assert "1 job" in text and "Backend Engineer" in text and "no atom mentions it" in text


def test_job_tool_truncates_the_description(db, config):
    text = run_tool("job", {"id": 1}, db, config=config)
    assert "Backend Engineer at Acme" in text and "fit 80" in text and "missing: Docker" in text
    assert len(text) < 1900


def test_unknown_tool_and_bad_args_are_error_strings(db, config):
    assert run_tool("teleport", {}, db, config=config).startswith("error:")
    assert run_tool("atom", {"id": "x"}, db, config=config).startswith("error:")


def test_cached_repos_hits_the_network_once_per_day(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(username, token=None):
        calls.append(username)
        return [
            {
                "name": "shortener",
                "full_name": "ada/shortener",
                "html_url": "https://github.com/ada/shortener",
                "language": "Python",
                "pushed_at": "2026-09-01T00:00:00Z",
                "readme": "# Shortener",
            }
        ]

    monkeypatch.setattr(tools, "fetch_repos", fake_fetch)
    repos, note = cached_repos("ada", None, tmp_path)
    again, _ = cached_repos("ada", None, tmp_path)
    assert repos == again and note == "" and calls == ["ada"]

    cache = tmp_path / "counselor" / "repos-ada.json"
    stale = json.loads(cache.read_text())
    stale["fetched_at"] = time.time() - tools.REPO_CACHE_TTL_SECONDS - 1
    cache.write_text(json.dumps(stale))
    cached_repos("ada", None, tmp_path)
    assert calls == ["ada", "ada"]


def test_cached_repos_without_username_or_on_failure_returns_a_note(tmp_path, monkeypatch):
    assert cached_repos(None, None, tmp_path) == ([], "no GitHub username configured")

    def boom(username, token=None):
        raise RuntimeError("rate limit")

    monkeypatch.setattr(tools, "fetch_repos", boom)
    repos, note = cached_repos("ada", None, tmp_path)
    assert repos == [] and "rate limit" in note


def test_repo_tool_reads_from_the_cache(db, config, monkeypatch):
    monkeypatch.setattr(
        tools,
        "fetch_repos",
        lambda u, token=None: [
            {
                "name": "shortener",
                "full_name": "ada/shortener",
                "html_url": "x",
                "language": "Python",
                "pushed_at": "2026-09-01T00:00:00Z",
                "readme": "# Shortener\nRedis-backed.",
            }
        ],
    )
    text = run_tool("repo", {"name": "shortener"}, db, config=config)
    assert "Redis-backed" in text and "Python" in text
    assert run_tool("repo", {"name": "nope"}, db, config=config).startswith("error:")
