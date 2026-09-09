import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.linkedin.fields import field_spec
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


def test_a_genuine_provider_failure_is_still_a_502(make_client):
    # EmptyProfileError subclasses LLMError, and the route must catch the
    # narrower exception first. Atoms ARE seeded here, so build_profile gets
    # past the empty-store check and reaches the LLM, where FakeProvider (given
    # no queued responses) raises a plain LLMError. If the except clauses were
    # ever reordered, this would start returning the empty-store page instead
    # of a 502 — pinning both tests together pins the ordering.
    client = make_client([])
    seed_atom(client)

    posted = client.post("/linkedin/generate", follow_redirects=False)

    assert posted.status_code == 502


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
    # compose_profile strips the text before storing it, so the length that
    # actually gets checked is the stripped length, not the raw one.
    long_headline = ("Python " * 60).strip()
    client = make_client([response(headline=long_headline)])
    seed_atom(client)
    client.post("/linkedin/generate", follow_redirects=False)

    page = client.get("/linkedin")
    limit = field_spec("headline").limit
    assert f"Too long for LinkedIn ({len(long_headline)} of {limit} characters)" in page.text


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


def test_an_older_version_is_rendered_read_only(make_client):
    client = make_client([response(headline="First"), response(headline="Second")])
    seed_atom(client)
    client.post("/linkedin/generate", follow_redirects=False)
    client.post("/linkedin/generate", follow_redirects=False)

    older = client.get("/linkedin/versions/1")
    assert older.status_code == 200
    assert "older version" in older.text.lower()
    assert 'href="/linkedin"' in older.text
    assert "<textarea" not in older.text
    assert ">Approve<" not in older.text


def test_an_unknown_version_is_a_404(make_client):
    client = make_client([])
    assert client.get("/linkedin/versions/999").status_code == 404


def test_editing_a_field_on_an_older_version_is_refused(make_client):
    client = make_client([response(headline="First"), response(headline="Second")])
    seed_atom(client)
    client.post("/linkedin/generate", follow_redirects=False)
    old_field_id = field_ids(client, "headline")[0]
    client.post("/linkedin/generate", follow_redirects=False)

    refused = client.post(f"/linkedin/fields/{old_field_id}", data={"text": "Rewritten"})

    assert refused.status_code == 409
    assert "older version" in refused.text.lower()


def test_approving_a_field_on_an_older_version_is_refused(make_client):
    client = make_client([response(headline="First"), response(headline="Second")])
    seed_atom(client)
    client.post("/linkedin/generate", follow_redirects=False)
    old_field_id = field_ids(client, "headline")[0]
    client.post("/linkedin/generate", follow_redirects=False)

    refused = client.post(f"/linkedin/fields/{old_field_id}/approve")

    assert refused.status_code == 409
    assert "older version" in refused.text.lower()


def test_editing_an_approved_field_withdraws_the_approval(make_client):
    client = make_client([response()])
    seed_atom(client)
    client.post("/linkedin/generate", follow_redirects=False)
    field_id = field_ids(client, "headline")[0]

    approved = client.post(f"/linkedin/fields/{field_id}/approve")
    assert approved.status_code == 200
    assert "Approved" in approved.text
    assert "Not approved yet" not in approved.text
    assert ">Approve<" not in approved.text

    edited = client.post(f"/linkedin/fields/{field_id}", data={"text": "Backend Engineer, Porto"})

    assert edited.status_code == 200
    assert "Not approved yet" in edited.text
    assert ">Approve<" in edited.text


def seed_atom_with_skills(client, skills):
    with Session(client.engine) as session:
        AtomStore(session).add(
            EvidenceAtom(
                kind=AtomKind.PROJECT,
                title="URL shortener",
                description="Built a link shortener in Flask, cached in Redis.",
                skills=skills,
                provenance=Provenance.INTERVIEW,
                confirmed=True,
            )
        )
        session.commit()


def test_a_skills_field_over_the_item_count_reports_items_not_characters(make_client):
    # Every entry is "Python" so verify_skills keeps all 60 of them: the
    # evidence atom itself lists "Python" as a skill.
    skills = ["Python"] * 60
    client = make_client([response(skills=skills)])
    seed_atom_with_skills(client, ["Python"])
    client.post("/linkedin/generate", follow_redirects=False)

    page = client.get("/linkedin")
    assert "60 skills, LinkedIn allows 50" in page.text
    assert "50 items" not in page.text


def test_a_skills_field_over_the_per_item_length_reports_the_item_limit(make_client):
    long_skill = "x" * 90
    skills = ["Python", long_skill]
    client = make_client([response(skills=skills)])
    seed_atom_with_skills(client, skills)
    client.post("/linkedin/generate", follow_redirects=False)

    page = client.get("/linkedin")
    assert "One skill is longer than 80 characters" in page.text
