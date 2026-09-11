import pytest
from fastapi.testclient import TestClient

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.llm import FakeProvider
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
        yield c


def test_browser_requests_get_the_error_page_in_the_layout(client):
    response = client.get("/jobs/999", headers={"Accept": "text/html"})

    assert response.status_code == 404
    assert "No such job" in response.text
    assert 'class="tabs"' in response.text
    assert 'href="/"' in response.text


def test_htmx_and_api_clients_keep_json(client):
    htmx = client.get("/jobs/999", headers={"Accept": "text/html", "HX-Request": "true"})
    assert htmx.status_code == 404
    assert htmx.json() == {"detail": "No such job"}

    plain = client.get("/jobs/999")
    assert plain.json() == {"detail": "No such job"}


def test_unknown_route_gets_the_error_page_too(client):
    response = client.get("/no-such-page", headers={"Accept": "text/html"})

    assert response.status_code == 404
    assert 'class="tabs"' in response.text
