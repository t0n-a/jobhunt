import fnmatch
import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from jobhunt.coach.models import QuestStep, VerificationType

logger = logging.getLogger(__name__)

API = "https://api.github.com"
TIMEOUT = 20


@dataclass
class VerificationResult:
    verified: bool
    detail: str


def verify_step(
    step: QuestStep, *, token: str | None = None, since: datetime | None = None
) -> VerificationResult:
    """Check whether a step's work actually landed.

    Never raises: an unreachable GitHub or an unusable spec degrades to
    "could not verify", and the caller falls back to self-report.
    """
    spec = step.verification or {}
    kind = spec.get("type", VerificationType.MANUAL.value)

    if kind == VerificationType.MANUAL.value:
        return VerificationResult(False, "Manual step — self-report when done.")

    repo = spec.get("repo")
    if not repo:
        return VerificationResult(False, "Verification spec has no repo — self-report.")

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
            if kind == VerificationType.GITHUB_REPO_EXISTS.value:
                return _repo_exists(client, repo)
            if kind == VerificationType.GITHUB_COMMIT.value:
                return _commit_touches(client, repo, spec.get("path_glob", "*"), since)
            if kind == VerificationType.GITHUB_FILE_CONTAINS.value:
                return _file_contains(client, repo, spec.get("path", ""), spec.get("text", ""))
    except httpx.HTTPError as exc:
        logger.warning("verification could not reach GitHub: %s", exc)
        return VerificationResult(False, "Could not reach GitHub — self-report instead.")

    return VerificationResult(False, "Unknown verification type — self-report.")


def _repo_exists(client: httpx.Client, repo: str) -> VerificationResult:
    response = client.get(f"{API}/repos/{repo}")
    if response.status_code == 200:
        return VerificationResult(True, f"{repo} exists.")
    return VerificationResult(False, f"{repo} not found yet.")


def _commit_touches(
    client: httpx.Client, repo: str, path_glob: str, since: datetime | None
) -> VerificationResult:
    params = {"per_page": 20}
    if since is not None:
        params["since"] = since.isoformat()
    response = client.get(f"{API}/repos/{repo}/commits", params=params)
    if response.status_code != 200:
        return VerificationResult(False, f"No commits readable in {repo}.")

    for commit in response.json():
        sha = commit.get("sha")
        if not sha:
            continue
        detail = client.get(f"{API}/repos/{repo}/commits/{sha}")
        if detail.status_code != 200:
            continue
        for changed in detail.json().get("files", []):
            filename = changed.get("filename", "")
            if fnmatch.fnmatch(filename, path_glob) or fnmatch.fnmatch(
                filename, path_glob.replace("**/", "")
            ):
                return VerificationResult(True, f"{sha[:7]} touched {filename}.")
    return VerificationResult(False, f"No commit in {repo} matched {path_glob} yet.")


def _file_contains(client: httpx.Client, repo: str, path: str, text: str) -> VerificationResult:
    if not path or not text:
        return VerificationResult(False, "Spec needs both a path and text — self-report.")
    response = client.get(
        f"{API}/repos/{repo}/contents/{path}",
        headers={"Accept": "application/vnd.github.raw"},
    )
    if response.status_code != 200:
        return VerificationResult(False, f"{path} not found in {repo} yet.")
    if text.casefold() in response.text.casefold():
        return VerificationResult(True, f"{path} contains the expected text.")
    return VerificationResult(False, f"{path} does not mention it yet.")
