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


def test_nav_lists_every_section_in_pipeline_order(client):
    html = client.get("/atoms").text
    labels = [
        "Home",
        "Today",
        "Jobs",
        "Gaps",
        "Applications",
        "LinkedIn",
        "Evidence",
        "Interview",
        "Import",
    ]
    positions = [html.index(f">{label}</a>") for label in labels]
    assert positions == sorted(positions)


def test_active_tab_is_marked_by_path_prefix(client):
    html = client.get("/atoms").text
    assert '<a href="/atoms" aria-current="page">Evidence</a>' in html
    assert 'aria-current="page">Jobs' not in html


def test_stylesheet_defines_the_tokens(client):
    css = client.get("/static/app.css").text
    for token in (
        "--bg",
        "--surface",
        "--surface-2",
        "--ink",
        "--ink-soft",
        "--line",
        "--accent",
        "--ok",
        "--ok-bg",
        "--err",
        "--warn-bg",
        "--warn-line",
        "--radius",
        "--shadow",
    ):
        assert f"{token}:" in css
    assert "prefers-color-scheme: dark" in css


def test_layout_ships_the_busy_button_script(client):
    html = client.get("/import").text
    assert "data-busy" in html
    assert "dataset.busy" in html
