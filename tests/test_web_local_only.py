from fastapi.testclient import TestClient

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.web.app import create_app


def client(tmp_path):
    cfg = Config(
        profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"),
        llm=LLMConfig(cache_dir=tmp_path / "cache"),
        data_dir=tmp_path,
    )
    return TestClient(create_app(cfg))


def test_cross_site_post_is_refused(tmp_path):
    c = client(tmp_path)
    r = c.post("/applications/1/approve", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = c.post("/applications/1/approve", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_same_origin_post_passes_the_guard(tmp_path):
    c = client(tmp_path)
    r = c.post("/applications/1/approve", headers={"Origin": "http://127.0.0.1:8000"})
    assert r.status_code == 404  # reached the route: no such application


def test_foreign_host_is_refused(tmp_path):
    c = client(tmp_path)
    assert c.get("/healthz", headers={"Host": "rebound.evil.example"}).status_code == 403
    assert c.get("/healthz", headers={"Host": "localhost:8000"}).status_code == 200
