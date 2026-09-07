import httpx
import pytest
import respx

from jobhunt.llm import FakeProvider
from jobhunt.profile.importers.github import fetch_repos, import_github
from jobhunt.profile.models import AtomKind, Provenance

API = "https://api.github.com"


def repo(name="url-shortener", fork=False, description="A URL shortener", language="Python"):
    return {
        "name": name,
        "full_name": f"ada/{name}",
        "html_url": f"https://github.com/ada/{name}",
        "description": description,
        "language": language,
        "topics": ["flask"],
        "stargazers_count": 3,
        "fork": fork,
    }


@respx.mock
def test_fetch_repos_excludes_forks():
    respx.get(f"{API}/users/ada/repos").mock(
        return_value=httpx.Response(200, json=[repo(), repo(name="forked", fork=True)])
    )
    respx.get(f"{API}/repos/ada/url-shortener/readme").mock(return_value=httpx.Response(404))

    repos = fetch_repos("ada")

    assert len(repos) == 1
    assert repos[0]["name"] == "url-shortener"


@respx.mock
def test_fetch_repos_attaches_readme_text():
    respx.get(f"{API}/users/ada/repos").mock(return_value=httpx.Response(200, json=[repo()]))
    respx.get(f"{API}/repos/ada/url-shortener/readme").mock(
        return_value=httpx.Response(200, text="# URL Shortener\nBuilt with Flask.")
    )

    repos = fetch_repos("ada")

    assert "Built with Flask" in repos[0]["readme"]


@respx.mock
def test_fetch_repos_sends_token_when_given():
    route = respx.get(f"{API}/users/ada/repos").mock(return_value=httpx.Response(200, json=[]))

    fetch_repos("ada", token="ghp_secret")

    assert route.calls[0].request.headers["authorization"] == "Bearer ghp_secret"


@respx.mock
def test_fetch_repos_raises_on_unknown_user():
    respx.get(f"{API}/users/nobody/repos").mock(return_value=httpx.Response(404))

    with pytest.raises(httpx.HTTPStatusError):
        fetch_repos("nobody")


@respx.mock
def test_rate_limit_raises_actionable_runtime_error():
    respx.get(f"{API}/users/ada/repos").mock(
        return_value=httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0"},
            json={"message": "API rate limit exceeded"},
        )
    )

    with pytest.raises(RuntimeError) as exc_info:
        fetch_repos("ada")

    assert "GITHUB_TOKEN" in str(exc_info.value)


@respx.mock
def test_import_github_builds_atoms():
    respx.get(f"{API}/users/ada/repos").mock(return_value=httpx.Response(200, json=[repo()]))
    respx.get(f"{API}/repos/ada/url-shortener/readme").mock(
        return_value=httpx.Response(200, text="Flask and Redis.")
    )
    llm = FakeProvider(
        [
            {
                "kind": "project",
                "title": "URL shortener",
                "description": "Flask service backed by Redis.",
                "skills": ["Python", "Flask", "Redis"],
                "metrics": None,
            }
        ]
    )

    atoms = import_github("ada", llm)

    assert len(atoms) == 1
    assert atoms[0].kind is AtomKind.PROJECT
    assert atoms[0].provenance is Provenance.GITHUB
    assert atoms[0].source_ref == "https://github.com/ada/url-shortener"
    assert atoms[0].confirmed is False


@respx.mock
def test_import_github_skips_repos_with_no_description_or_readme():
    respx.get(f"{API}/users/ada/repos").mock(
        return_value=httpx.Response(200, json=[repo(description=None)])
    )
    respx.get(f"{API}/repos/ada/url-shortener/readme").mock(return_value=httpx.Response(404))
    llm = FakeProvider([])

    atoms = import_github("ada", llm)

    assert atoms == []
    assert llm.prompts == []
