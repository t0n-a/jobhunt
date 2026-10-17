import base64
import json
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from jobhunt.db import create_engine_for, init_db
from jobhunt.growth.gh import Gh, GhError
from jobhunt.growth.models import Suggestion, upsert_suggestion


class FakeRun:
    """Records each argv and returns canned output, so no test ever runs the real gh."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.calls = []
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "g.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def test_repos_builds_the_command_and_parses_json():
    fake = FakeRun(stdout=json.dumps([{"name": "app", "nameWithOwner": "me/app"}]))
    repos = Gh(run=fake).repos("me", "public")
    assert repos == [{"name": "app", "nameWithOwner": "me/app"}]
    argv, kwargs = fake.calls[0]
    assert argv[:6] == ["gh", "repo", "list", "me", "--visibility", "public"]
    assert "--limit" in argv and "200" in argv
    assert kwargs == {"capture_output": True, "text": True, "timeout": 120}


def test_nonzero_exit_raises_gh_error_with_stderr():
    fake = FakeRun(returncode=1, stderr="  boom: no auth  \n")
    with pytest.raises(GhError, match="boom: no auth"):
        Gh(run=fake).languages("me/app")


def test_missing_gh_is_a_gh_error():
    def no_gh(argv, **kwargs):
        raise FileNotFoundError("gh")

    with pytest.raises(GhError, match="gh is not installed"):
        Gh(run=no_gh).repos("me", "public")


@pytest.mark.parametrize("bad", ["a/b; rm -rf", "--help", "a b", "x/y/z"])
def test_bad_repo_names_are_refused(bad):
    fake = FakeRun()
    with pytest.raises(GhError, match="bad repository name"):
        Gh(run=fake).languages(bad)
    assert fake.calls == []


def test_put_file_sends_base64_and_sha():
    fake = FakeRun()
    Gh(run=fake).put_file("me/app", "README.md", "héllo", "add readme", sha="abc123")
    argv = fake.calls[0][0]
    assert argv[:5] == ["gh", "api", "-X", "PUT", "repos/me/app/contents/README.md"]
    assert "message=add readme" in argv
    assert f"content={base64.b64encode('héllo'.encode()).decode()}" in argv
    assert "sha=abc123" in argv


def test_readme_404_is_empty():
    fake = FakeRun(returncode=1, stderr="gh: Not Found (HTTP 404)")
    assert Gh(run=fake).readme("me/app") == ("", None)


def test_readme_decodes_content_and_sha():
    payload = {"content": base64.b64encode(b"# App\n").decode(), "sha": "deadbeef"}
    fake = FakeRun(stdout=json.dumps(payload))
    assert Gh(run=fake).readme("me/app") == ("# App\n", "deadbeef")


def test_upsert_never_reopens_declined(session):
    first = upsert_suggestion(session, "publish", "me/app", title="Publish app")
    first.status = "declined"
    session.commit()

    again = upsert_suggestion(session, "publish", "me/app", title="Other", body="new")

    assert again.id == first.id
    assert again.status == "declined"
    assert again.title == "Publish app"
    assert session.query(Suggestion).count() == 1
