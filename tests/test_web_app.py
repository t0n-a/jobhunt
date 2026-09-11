import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.llm import FakeProvider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore
from jobhunt.web.app import create_app
from jobhunt.web.deps import get_llm


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
        yield c


def add_atom(client, **overrides) -> EvidenceAtom:
    init_db(client.engine)
    defaults = {
        "kind": AtomKind.PROJECT,
        "title": "URL shortener",
        "description": "Flask and Redis.",
        "skills": ["Python"],
        "provenance": Provenance.INTERVIEW,
    }
    defaults.update(overrides)
    with Session(client.engine) as session:
        return AtomStore(session).add(EvidenceAtom(**defaults))


def test_healthz_returns_ok(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_renders_home(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Evidence" in response.text


def test_atoms_page_renders_empty_state(client):
    response = client.get("/atoms")

    assert response.status_code == 200
    assert "No evidence yet" in response.text


def test_atoms_page_lists_atom_titles(client):
    add_atom(client, title="URL shortener")

    response = client.get("/atoms")

    assert response.status_code == 200
    assert "URL shortener" in response.text


def test_atoms_page_shows_profile_name(client):
    response = client.get("/atoms")

    assert "Ada" in response.text
