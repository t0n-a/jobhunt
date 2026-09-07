import httpx

from jobhunt.llm import LLMProvider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance

API = "https://api.github.com"
README_CHARS = 4000

REPO_ATOM_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["project", "experience", "education", "course", "skill"],
        },
        "title": {"type": "string"},
        "description": {"type": "string"},
        "skills": {"type": "array", "items": {"type": "string"}},
        "metrics": {"type": ["string", "null"]},
    },
    "required": ["kind", "title", "description", "skills"],
}

PROMPT = """\
Summarise this GitHub repository as a single CV evidence atom.

Rules:
- Do not invent anything. Use only the description and README below. If the
  repository is a tutorial follow-along or has no substance, still summarise it
  factually rather than inflating it.
- "description" is one or two factual sentences about what was built and how.
- "skills" lists technologies actually evidenced here.
- "metrics" is null unless the README states a real number.

Repository: {full_name}
Primary language: {language}
Topics: {topics}
Description: {description}

README (truncated):
---
{readme}
---
"""


def _check_rate_limit(response: httpx.Response) -> None:
    if response.status_code in (403, 429):
        is_rate_limit = (
            response.headers.get("X-RateLimit-Remaining") == "0"
            or "rate limit" in response.text.lower()
        )
        if is_rate_limit:
            raise RuntimeError(
                "GitHub API rate limit exceeded. Set the GITHUB_TOKEN environment variable "
                "to raise the rate limit from 60 to 5000 requests per hour."
            )


def fetch_repos(username: str, token: str | None = None) -> list[dict]:
    """Fetch a user's non-fork public repos, each with its README text."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(headers=headers, timeout=30) as client:
        response = client.get(
            f"{API}/users/{username}/repos",
            params={"per_page": 100, "sort": "updated"},
        )
        _check_rate_limit(response)
        response.raise_for_status()

        repos = [r for r in response.json() if not r.get("fork")]
        for repo in repos:
            repo["readme"] = _fetch_readme(client, repo["full_name"])
        return repos


def _fetch_readme(client: httpx.Client, full_name: str) -> str:
    response = client.get(
        f"{API}/repos/{full_name}/readme",
        headers={"Accept": "application/vnd.github.raw"},
    )
    _check_rate_limit(response)
    if response.status_code != 200:
        return ""
    return response.text[:README_CHARS]


def import_github(username: str, llm: LLMProvider, token: str | None = None) -> list[EvidenceAtom]:
    """Turn a user's repositories into unconfirmed evidence atoms."""
    atoms: list[EvidenceAtom] = []

    for repo in fetch_repos(username, token):
        description = repo.get("description") or ""
        readme = repo.get("readme") or ""
        if not description and not readme.strip():
            continue

        response = llm.complete(
            PROMPT.format(
                full_name=repo["full_name"],
                language=repo.get("language") or "unknown",
                topics=", ".join(repo.get("topics") or []) or "none",
                description=description or "(none)",
                readme=readme or "(none)",
            ),
            schema=REPO_ATOM_SCHEMA,
        )
        if not isinstance(response, dict):
            continue

        try:
            atoms.append(
                EvidenceAtom(
                    kind=_coerce_kind(response.get("kind")),
                    title=response["title"],
                    description=response["description"],
                    skills=list(response.get("skills") or []),
                    metrics=response.get("metrics"),
                    provenance=Provenance.GITHUB,
                    source_ref=repo["html_url"],
                    confirmed=False,
                )
            )
        except (KeyError, TypeError):
            continue

    return atoms


def _coerce_kind(value: str | None) -> AtomKind:
    try:
        return AtomKind(value)
    except ValueError:
        return AtomKind.PROJECT
