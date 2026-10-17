import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, posting_hash
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
        app.dependency_overrides[get_llm] = lambda: FakeProvider(responses)
        client = TestClient(app)
        client.engine = create_engine_for(tmp_path / "jobhunt.db")
        init_db(client.engine)
        return client

    return _make


def seed(client) -> int:
    with Session(client.engine) as session:
        description = "We need Python and Redis."
        job = Job(
            title="Backend Engineer",
            company="Acme",
            description=description,
            url="u",
            content_hash=posting_hash("Backend Engineer", "Acme", description),
        )
        session.add(job)
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
        session.refresh(job)
        return job.id


def responses(bullets=None):
    resume = {
        "headline": "Backend Engineer",
        "summary": "",
        "sections": [
            {
                "name": "Projects",
                "entries": [
                    {
                        "atom_id": 1,
                        "title": "URL shortener",
                        "org": "Personal",
                        "dates": "2026",
                        "bullets": bullets
                        or [{"atom_id": 1, "text": "Cached responses in Redis."}],
                    }
                ],
            }
        ],
        "skills": ["Python", "Redis"],
    }
    return [
        {"keywords": ["Python", "Redis"]},
        resume,
        {"cover_letter": "Dear Acme team, I built a link shortener."},
    ]


def test_applications_page_is_empty_at_first(make_client):
    client = make_client([])

    assert "no applications" in client.get("/applications").text.casefold()


def test_tailoring_a_job_creates_an_application(make_client):
    client = make_client(responses())
    job_id = seed(client)

    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)

    assert "Acme" in client.get("/applications").text


def test_the_detail_page_shows_the_bullets_and_the_letter(make_client):
    client = make_client(responses())
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)

    text = client.get("/applications/1").text

    assert "Cached responses in Redis." in text
    assert "Dear Acme team" in text


def test_dropped_bullets_are_shown_with_their_reason(make_client):
    """A rejected bullet on screen is how the user tells a verifier bug from a
    model that is inventing."""
    client = make_client(
        responses(
            [
                {"atom_id": 1, "text": "Cached responses in Redis."},
                {"atom_id": 1, "text": "Cut latency by 40%."},
            ]
        )
    )
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)

    text = client.get("/applications/1").text

    assert "Cut latency by 40%." in text
    # The REASON, not just the text: asserting a substring of the bullet itself
    # would pass against a template that dropped the explanation entirely.
    assert "evidence does not" in text


def test_editing_a_bullet_marks_it_as_the_users_own(make_client):
    client = make_client(responses())
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)

    client.post("/applications/1/bullets/0", data={"text": "Cached responses in Redis for a day."})
    text = client.get("/applications/1").text

    assert "Cached responses in Redis for a day." in text
    assert "yours" in text.casefold()


def test_deleting_a_bullet_removes_it(make_client):
    client = make_client(responses())
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)

    client.post("/applications/1/bullets/0/delete")
    text = client.get("/applications/1").text

    assert "Cached responses in Redis." not in text


def test_marking_applied_records_it(make_client):
    client = make_client(responses())
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)

    client.post("/applications/1/applied", follow_redirects=True)

    assert "sent" in client.get("/applications").text.casefold()


def test_tailoring_without_confirmed_evidence_is_a_clear_error(make_client, tmp_path):
    client = make_client(responses())
    with Session(client.engine) as session:
        description = "We need Python."
        job = Job(
            title="Backend Engineer",
            company="Acme",
            description=description,
            url="u",
            content_hash=posting_hash("Backend Engineer", "Acme", description),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    response = client.post(f"/jobs/{job_id}/tailor", follow_redirects=False)

    assert response.status_code == 502


def test_the_pdf_route_404s_before_anything_is_rendered(make_client):
    client = make_client(responses())
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)

    assert client.get("/applications/1/pdf").status_code == 404


def test_the_unverified_header_fields_are_shown_for_review(make_client):
    """They are model-written and the verifier does not check them, so the only
    safeguard is the user reading them, which needs them on the page."""
    client = make_client(responses())
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)

    text = client.get("/applications/1").text

    assert "Backend Engineer" in text
    assert "not checked against your evidence" in text.casefold()
