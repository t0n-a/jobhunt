from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.llm import FakeProvider
from jobhunt.profile.importers import cv as cv_mod
from jobhunt.profile.interview import InterviewSession
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore
from jobhunt.web.app import create_app
from jobhunt.web.deps import get_llm


@pytest.fixture
def make_client(tmp_path: Path):
    def _make(responses):
        cfg = Config(
            profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"),
            llm=LLMConfig(cache_dir=tmp_path / "cache"),
            data_dir=tmp_path,
        )
        app = create_app(cfg)
        provider = FakeProvider(responses)
        app.dependency_overrides[get_llm] = lambda: provider
        client = TestClient(app)
        client.engine = create_engine_for(tmp_path / "jobhunt.db")
        init_db(client.engine)
        return client

    return _make


def seed_atom(client: TestClient, **overrides) -> EvidenceAtom:
    defaults = {
        "kind": AtomKind.PROJECT,
        "title": "Original",
        "description": "D",
        "skills": ["Python"],
        "provenance": Provenance.CV,
    }
    defaults.update(overrides)
    with Session(client.engine) as session:
        return AtomStore(session).add(EvidenceAtom(**defaults))


def test_import_page_renders(make_client):
    client = make_client([])

    assert client.get("/import").status_code == 200


def test_interview_page_renders(make_client):
    client = make_client(["Q1"])

    assert client.get("/interview").status_code == 200

    client.post("/interview", data={"topic": "Zephyr URL shortener"}, follow_redirects=True)
    response = client.get("/interview")
    assert response.status_code == 200
    assert "Zephyr URL shortener" in response.text


def test_cv_upload_creates_atoms(make_client):
    client = make_client(
        [
            {
                "atoms": [
                    {
                        "kind": "project",
                        "title": "Uploaded project",
                        "description": "D",
                        "skills": ["Python"],
                        "metrics": None,
                    }
                ]
            }
        ]
    )

    response = client.post(
        "/import/cv",
        files={"file": ("cv.txt", b"URL Shortener - Flask and Redis", "text/plain")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Uploaded project" in response.text


def test_cv_upload_rejects_unsupported_format(make_client):
    client = make_client([])

    response = client.post("/import/cv", files={"file": ("cv.rtf", b"x", "application/rtf")})

    assert response.status_code == 400
    assert "Unsupported CV format" in response.json()["detail"]


def test_upload_with_dot_filename_returns_400_not_500(make_client):
    client = make_client([])

    response = client.post("/import/cv", files={"file": (".", b"x", "text/plain")})

    assert response.status_code == 400


def test_cv_upload_llm_error_returns_502(make_client):
    client = make_client(["not a dict"])

    response = client.post(
        "/import/cv",
        files={"file": ("cv.txt", b"URL Shortener - Flask and Redis", "text/plain")},
    )

    assert response.status_code == 502
    assert "CV extraction expected a JSON object" in response.json()["detail"]


def test_cv_upload_runtime_error_returns_503(make_client, monkeypatch):
    def _fail(_path):
        raise RuntimeError(
            "pdftotext was not found while processing cv.pdf. poppler-utils is required to parse PDF files."
        )

    monkeypatch.setattr(cv_mod, "extract_text", _fail)
    client = make_client([])

    response = client.post(
        "/import/cv",
        files={"file": ("cv.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 503
    assert "pdftotext was not found" in response.json()["detail"]


@respx.mock
def test_github_import_creates_atoms(make_client):
    respx.get("https://api.github.com/users/ada/repos").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "name": "r",
                    "full_name": "ada/r",
                    "html_url": "https://github.com/ada/r",
                    "description": "A thing",
                    "language": "Python",
                    "topics": [],
                    "stargazers_count": 0,
                    "fork": False,
                }
            ],
        )
    )
    respx.get("https://api.github.com/repos/ada/r/readme").mock(return_value=httpx.Response(404))
    client = make_client(
        [
            {
                "kind": "project",
                "title": "GitHub project",
                "description": "D",
                "skills": ["Python"],
                "metrics": None,
            }
        ]
    )

    response = client.post("/import/github", data={"username": "ada"}, follow_redirects=True)

    assert "GitHub project" in response.text


def test_coursework_import_creates_atoms(make_client):
    client = make_client(
        [
            {
                "atoms": [
                    {
                        "title": "Distributed Systems",
                        "description": "D",
                        "skills": ["Consensus"],
                    }
                ]
            }
        ]
    )

    response = client.post(
        "/import/coursework",
        data={"text": "Distributed Systems", "institution": "ISEP"},
        follow_redirects=True,
    )

    assert "Distributed Systems" in response.text


def test_starting_an_interview_shows_the_first_question(make_client):
    client = make_client(["What did you build?"])

    response = client.post("/interview", data={"topic": "URL shortener"}, follow_redirects=True)

    assert "What did you build?" in response.text


def test_interview_start_maps_llm_error_to_502(make_client):
    client = make_client([])
    response = client.post("/interview", data={"topic": "T"})
    assert response.status_code == 502


def test_answering_advances_to_the_next_question(make_client):
    client = make_client(["Q1", "Q2"])
    client.post("/interview", data={"topic": "T"}, follow_redirects=True)

    response = client.post("/interview/1/answer", data={"answer": "A1"})

    assert response.status_code == 200
    assert "Q2" in response.text


def test_answer_is_saved_even_if_next_question_fails(make_client):
    client = make_client(["Q1"])
    client.post("/interview", data={"topic": "T"}, follow_redirects=True)

    response = client.post("/interview/1/answer", data={"answer": "Saved answer"})
    assert response.status_code == 502

    with Session(client.engine) as session:
        stored = session.get(InterviewSession, 1)
        assert stored is not None
        assert len(stored.turns) == 1
        assert stored.turns[0]["answer"] == "Saved answer"


def test_harvest_creates_atoms_and_redirects(make_client):
    client = make_client(
        [
            "Q1",
            "Q2",
            {
                "atoms": [
                    {
                        "kind": "project",
                        "title": "Harvested",
                        "description": "D",
                        "skills": [],
                        "metrics": None,
                    }
                ]
            },
        ]
    )
    client.post("/interview", data={"topic": "T"}, follow_redirects=True)
    client.post("/interview/1/answer", data={"answer": "A1"})

    response = client.post("/interview/1/harvest", follow_redirects=True)

    assert "Harvested" in response.text


def test_harvest_twice_does_not_create_duplicate_atoms(make_client):
    client = make_client(
        [
            "Q1",
            "Q2",
            {
                "atoms": [
                    {
                        "kind": "project",
                        "title": "Harvested",
                        "description": "D",
                        "skills": [],
                        "metrics": None,
                    }
                ]
            },
        ]
    )
    client.post("/interview", data={"topic": "T"}, follow_redirects=True)
    client.post("/interview/1/answer", data={"answer": "A1"})

    res1 = client.post("/interview/1/harvest", follow_redirects=True)
    assert res1.status_code == 200
    assert "Harvested" in res1.text

    # Second harvest call must be a no-op that redirects cleanly without duplicating atoms
    res2 = client.post("/interview/1/harvest", follow_redirects=True)
    assert res2.status_code == 200

    with Session(client.engine) as session:
        atoms = AtomStore(session).list()
        assert len(atoms) == 1
        assert atoms[0].title == "Harvested"


def test_harvest_failure_leaves_interview_retryable(make_client, monkeypatch):
    client = make_client(
        [
            "Q1",
            "Q2",
            {
                "atoms": [
                    {
                        "kind": "project",
                        "title": "Harvested",
                        "description": "D",
                        "skills": [],
                        "metrics": None,
                    }
                ]
            },
        ]
    )
    client.post("/interview", data={"topic": "T"}, follow_redirects=True)
    client.post("/interview/1/answer", data={"answer": "A1"})

    def _fail_add_many(self, atoms):
        raise RuntimeError("store failed")

    monkeypatch.setattr(AtomStore, "add_many", _fail_add_many)

    with pytest.raises(RuntimeError, match="store failed"):
        client.post("/interview/1/harvest")

    with Session(client.engine) as session:
        interview = session.get(InterviewSession, 1)
        assert interview is not None
        assert interview.status == "open"


def test_confirm_marks_atom_confirmed(make_client):
    client = make_client([])
    atom = seed_atom(client)

    response = client.post(f"/atoms/{atom.id}/confirm")

    assert response.status_code == 200
    assert "unconfirmed" not in response.text
    with Session(client.engine) as session:
        stored = AtomStore(session).get(atom.id)
        assert stored is not None
        assert stored.confirmed is True


def test_edit_updates_title_and_skills(make_client):
    client = make_client([])
    atom = seed_atom(client)

    response = client.post(
        f"/atoms/{atom.id}/edit",
        data={"title": "Renamed", "description": "New", "skills": "Go, Docker"},
    )

    assert "Renamed" in response.text
    with Session(client.engine) as session:
        stored = AtomStore(session).get(atom.id)
        assert stored is not None
        assert stored.skills == ["Go", "Docker"]


def test_delete_removes_atom(make_client):
    client = make_client([])
    atom = seed_atom(client)

    response = client.post(f"/atoms/{atom.id}/delete")

    assert response.status_code == 200
    assert response.text == ""
    with Session(client.engine) as session:
        assert AtomStore(session).get(atom.id) is None


def test_atom_with_html_in_title_is_escaped(make_client):
    client = make_client([])
    seed_atom(client, title="<script>alert('x')</script>")

    response = client.get("/atoms")

    assert response.status_code == 200
    assert "<script>alert('x')</script>" not in response.text
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_confirm_unknown_atom_returns_404(make_client):
    client = make_client([])
    response = client.post("/atoms/99999/confirm")
    assert response.status_code == 404


def test_edit_unknown_atom_returns_404(make_client):
    client = make_client([])
    response = client.post(
        "/atoms/99999/edit",
        data={"title": "Nonexistent", "description": "D", "skills": "Python"},
    )
    assert response.status_code == 404


def test_answering_twice_returns_400(make_client, monkeypatch):
    monkeypatch.setattr("jobhunt.profile.interview.MAX_TURNS", 1)
    client = make_client(["Q1"])
    client.post("/interview", data={"topic": "T"}, follow_redirects=True)

    res1 = client.post("/interview/1/answer", data={"answer": "A1"})
    assert res1.status_code == 200

    res2 = client.post("/interview/1/answer", data={"answer": "A2"})
    assert res2.status_code == 400


@respx.mock
def test_github_import_http_error_returns_400(make_client):
    respx.get("https://api.github.com/users/nobody/repos").mock(return_value=httpx.Response(404))
    client = make_client([])

    response = client.post("/import/github", data={"username": "nobody"})

    assert response.status_code == 400
