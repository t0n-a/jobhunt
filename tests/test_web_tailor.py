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


def _queue(client):
    from jobhunt.apply.tracker import set_status
    from jobhunt.tailor.models import Application

    with Session(client.engine) as db:
        set_status(db, db.get(Application, 1), "queued")


def test_queued_application_can_be_approved(make_client):
    client = make_client(responses())
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)
    _queue(client)

    response = client.post("/applications/1/approve", follow_redirects=True)

    assert "Approved." in response.text
    from jobhunt.tailor.models import Application

    with Session(client.engine) as db:
        assert db.get(Application, 1).status == "approved"


def test_approving_a_non_queued_application_is_409(make_client):
    client = make_client(responses())
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)

    assert client.post("/applications/1/approve").status_code == 409


def _set(client, status):
    from jobhunt.apply.tracker import set_status
    from jobhunt.tailor.models import Application

    with Session(client.engine) as db:
        set_status(db, db.get(Application, 1), status)


def test_status_route_follows_the_transition_table(make_client):
    client = make_client(responses())
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)
    _set(client, "sent")

    response = client.post("/applications/1/status", data={"status": "replied"}, follow_redirects=True)

    assert "Marked as replied." in response.text
    assert client.post("/applications/1/status", data={"status": "offer"}).status_code == 409
    assert client.post("/applications/1/status", data={"status": "bogus"}).status_code == 409


def test_detail_page_offers_the_allowed_next_statuses(make_client):
    client = make_client(responses())
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)
    _set(client, "replied")

    html = client.get("/applications/1").text

    assert 'name="status" value="interview"' in html
    assert 'name="status" value="rejected"' in html
    assert 'name="status" value="offer"' not in html


def test_opening_a_queued_application_marks_it_reviewed(make_client):
    from jobhunt.apply.queue import trust_count
    from jobhunt.tailor.models import Application

    client = make_client(responses())
    job_id = seed(client)
    client.post(f"/jobs/{job_id}/tailor", follow_redirects=True)
    _queue(client)
    with Session(client.engine) as db:
        assert db.get(Application, 1).reviewed_at is None

    client.get("/applications/1")
    client.post("/applications/1/approve")

    with Session(client.engine) as db:
        assert db.get(Application, 1).reviewed_at is not None
        assert trust_count(db) == 1


def test_review_page_shows_form_answers(make_client):
    from jobhunt.apply.form import FormField
    from jobhunt.apply.plan import FormPlan, Planned
    from jobhunt.tailor.models import Application

    client = make_client([])
    job_id = seed(client)
    plan = FormPlan(
        filled=[Planned("email", "a@example.com", "profile")],
        unknown=[FormField("why", "Why us?", "text", True)],
        dropped=[{"name": "x", "answer": "I led NASA", "reason": "not supported"}],
    )
    with Session(client.engine) as s:
        app = Application(job_id=job_id, status="queued", resume_json={}, form_plan=plan.to_json())
        s.add(app)
        s.commit()
        app_id = app.id
    html = client.get(f"/applications/{app_id}").text
    assert "Form answers" in html and "a@example.com" in html and "profile" in html
    assert "Why us?" in html and "you answer this" in html
    assert "I led NASA" in html and "not supported" in html
