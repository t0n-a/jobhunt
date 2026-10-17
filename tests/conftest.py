import pytest


@pytest.fixture(autouse=True)
def _allow_testclient_host(monkeypatch):
    """TestClient sends Host: testserver; production only answers on localhost."""
    from jobhunt.web import app

    monkeypatch.setattr(app, "LOCAL_HOSTS", app.LOCAL_HOSTS | {"testserver"})
