import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.llm import FakeProvider
from jobhunt.profile.models import EvidenceAtom
from jobhunt.web.app import create_app
from jobhunt.web.deps import get_llm


@pytest.fixture
def make_client(tmp_path):
    def _make(responses):
        cfg = Config(
            profile=ProfileConfig(name="Ada", email="ada@example.com", location="Porto"),
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


COURSEWORK = {
    "atoms": [
        {
            "kind": "course",
            "title": "Distributed Systems",
            "description": "Consensus.",
            "skills": ["Raft"],
            "metrics": None,
        }
    ]
}


def test_import_sets_a_flash_that_the_next_page_shows_once(make_client):
    client = make_client([COURSEWORK])

    redirect = client.post(
        "/import/coursework", data={"text": "x", "institution": "ISEP"}, follow_redirects=False
    )
    assert redirect.status_code == 303
    assert "jobhunt_flash" in redirect.cookies

    shown = client.get("/atoms")
    assert "1 atoms imported — confirm the ones that are right." in shown.text

    again = client.get("/atoms")
    assert "atoms imported" not in again.text


def test_empty_import_flashes_a_warning(make_client):
    client = make_client([{"atoms": []}])
    client.post(
        "/import/coursework", data={"text": "x", "institution": "ISEP"}, follow_redirects=False
    )

    shown = client.get("/atoms")
    assert 'class="flash warn"' in shown.text
    assert "Nothing found to import." in shown.text


def test_htmx_requests_never_carry_the_flash(make_client):
    client = make_client([COURSEWORK])
    client.post(
        "/import/coursework", data={"text": "x", "institution": "ISEP"}, follow_redirects=False
    )
    with Session(client.engine) as db:
        atom_id = db.exec(select(EvidenceAtom)).first().id

    partial = client.post(f"/atoms/{atom_id}/confirm", headers={"HX-Request": "true"})
    assert "atoms imported" not in partial.text
    # The flash is still pending for the next full page.
    assert "atoms imported" in client.get("/atoms").text
