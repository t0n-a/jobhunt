import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.linkedin.models import LinkedinField
from jobhunt.llm import FakeProvider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore
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
        provider = FakeProvider(responses)
        app.dependency_overrides[get_llm] = lambda: provider
        client = TestClient(app)
        client.engine = create_engine_for(tmp_path / "jobhunt.db")
        init_db(client.engine)
        return client

    return _make


def seed_atom(client):
    with Session(client.engine) as session:
        AtomStore(session).add(
            EvidenceAtom(
                kind=AtomKind.PROJECT,
                title="URL shortener",
                description="Built a link shortener in Flask, cached in Redis.",
                skills=["Python", "Flask", "Redis"],
                provenance=Provenance.INTERVIEW,
                confirmed=True,
            )
        )
        session.commit()


def response(atom_id=1, **overrides) -> dict:
    payload = {
        "headline": "Backend Engineer",
        "about": "Python developer who built a Flask service cached in Redis.",
        "experience": [],
        "projects": [{"atom_id": atom_id, "title": "Shortener", "text": "Built it in Flask."}],
        "skills": ["Python", "Flask"],
    }
    payload.update(overrides)
    return payload


def field_ids(client, name):
    with Session(client.engine) as session:
        rows = session.exec(select(LinkedinField).where(LinkedinField.name == name)).all()
        return [row.id for row in rows]


def test_the_page_loads_with_nothing_built(make_client):
    client = make_client([])
    page = client.get("/linkedin")
    assert page.status_code == 200
    assert "Build" in page.text


def test_generating_redirects_to_the_page_and_shows_the_fields(make_client):
    client = make_client([response()])
    seed_atom(client)

    posted = client.post("/linkedin/generate", follow_redirects=False)
    assert posted.status_code == 303

    page = client.get("/linkedin")
    assert "Backend Engineer" in page.text
    assert "Python developer" in page.text


def test_generating_with_no_evidence_explains_itself_instead_of_erroring(make_client):
    client = make_client([])  # no atoms seeded

    posted = client.post("/linkedin/generate", follow_redirects=True)

    assert posted.status_code == 200
    assert "confirm" in posted.text.lower()


def test_editing_a_field_returns_the_partial_and_marks_it_yours(make_client):
    client = make_client([response()])
    seed_atom(client)
    client.post("/linkedin/generate", follow_redirects=False)
    field_id = field_ids(client, "headline")[0]

    edited = client.post(f"/linkedin/fields/{field_id}", data={"text": "Backend Engineer, Porto"})

    assert edited.status_code == 200
    assert "Backend Engineer, Porto" in edited.text
    assert "Yours" in edited.text


def test_approving_a_field_returns_the_partial_marked_approved(make_client):
    client = make_client([response()])
    seed_atom(client)
    client.post("/linkedin/generate", follow_redirects=False)
    field_id = field_ids(client, "headline")[0]

    approved = client.post(f"/linkedin/fields/{field_id}/approve")

    assert approved.status_code == 200
    assert "Approved" in approved.text


def test_an_unknown_field_is_a_404(make_client):
    client = make_client([])
    assert client.post("/linkedin/fields/999/approve").status_code == 404
    assert client.post("/linkedin/fields/999", data={"text": "x"}).status_code == 404


def test_an_over_length_field_says_so_on_the_page(make_client):
    client = make_client([response(headline="Python " * 60)])
    seed_atom(client)
    client.post("/linkedin/generate", follow_redirects=False)

    page = client.get("/linkedin")
    assert "over LinkedIn" in page.text.lower() or "too long" in page.text.lower()


def test_rejects_are_shown_on_the_page(make_client):
    client = make_client([response(about="Served 90000 monthly users.")])
    seed_atom(client)
    client.post("/linkedin/generate", follow_redirects=False)

    page = client.get("/linkedin")
    assert "90000" in page.text


def test_an_older_version_is_readable(make_client):
    client = make_client([response(headline="First"), response(headline="Second")])
    seed_atom(client)
    client.post("/linkedin/generate", follow_redirects=False)
    client.post("/linkedin/generate", follow_redirects=False)

    page = client.get("/linkedin")
    assert "Second" in page.text

    older = client.get("/linkedin/versions/1")
    assert older.status_code == 200
    assert "First" in older.text


def test_an_unknown_version_is_a_404(make_client):
    client = make_client([])
    assert client.get("/linkedin/versions/999").status_code == 404
