from datetime import UTC, datetime

import httpx
import respx

from jobhunt.coach.models import QuestStep
from jobhunt.coach.verify import verify_step

API = "https://api.github.com"


def step(verification: dict) -> QuestStep:
    return QuestStep(quest_id=1, ordinal=1, title="t", instructions="i", verification=verification)


def test_manual_steps_are_never_auto_verified():
    result = verify_step(step({"type": "manual"}))

    assert result.verified is False
    assert "self-report" in result.detail.casefold()


@respx.mock
def test_repo_exists_is_verified():
    respx.get(f"{API}/repos/ada/k8s-lab").mock(return_value=httpx.Response(200, json={}))

    result = verify_step(step({"type": "github_repo_exists", "repo": "ada/k8s-lab"}))

    assert result.verified is True


@respx.mock
def test_a_missing_repo_is_not_verified():
    respx.get(f"{API}/repos/ada/nope").mock(return_value=httpx.Response(404))

    assert verify_step(step({"type": "github_repo_exists", "repo": "ada/nope"})).verified is False


@respx.mock
def test_a_matching_commit_verifies():
    respx.get(f"{API}/repos/ada/k8s-lab/commits").mock(
        return_value=httpx.Response(
            200,
            json=[{"sha": "abc", "commit": {"message": "add manifests"}}],
        )
    )
    respx.get(f"{API}/repos/ada/k8s-lab/commits/abc").mock(
        return_value=httpx.Response(200, json={"files": [{"filename": "k8s/deploy.yaml"}]})
    )

    result = verify_step(
        step({"type": "github_commit", "repo": "ada/k8s-lab", "path_glob": "**/*.yaml"}),
        since=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result.verified is True


@respx.mock
def test_a_commit_touching_no_matching_path_does_not_verify():
    respx.get(f"{API}/repos/ada/k8s-lab/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "abc", "commit": {"message": "docs"}}])
    )
    respx.get(f"{API}/repos/ada/k8s-lab/commits/abc").mock(
        return_value=httpx.Response(200, json={"files": [{"filename": "README.md"}]})
    )

    result = verify_step(
        step({"type": "github_commit", "repo": "ada/k8s-lab", "path_glob": "**/*.yaml"})
    )

    assert result.verified is False


@respx.mock
def test_file_contains_verifies_on_substring():
    respx.get(f"{API}/repos/ada/k8s-lab/contents/README.md").mock(
        return_value=httpx.Response(200, text="# k8s lab\nreadiness probe configured\n")
    )

    result = verify_step(
        step(
            {
                "type": "github_file_contains",
                "repo": "ada/k8s-lab",
                "path": "README.md",
                "text": "readiness probe",
            }
        )
    )

    assert result.verified is True


@respx.mock
def test_a_github_outage_reports_could_not_verify_rather_than_failing_the_step():
    """A coach that refuses to mark work done because an API is down is a coach
    the user stops opening. Fall back to self-report."""
    respx.get(url__startswith=API).mock(side_effect=httpx.ConnectError("down"))

    result = verify_step(step({"type": "github_repo_exists", "repo": "ada/k8s-lab"}))

    assert result.verified is False
    assert "could not" in result.detail.casefold()


def test_a_spec_missing_its_repo_degrades_to_self_report():
    result = verify_step(step({"type": "github_commit"}))

    assert result.verified is False
    assert "self-report" in result.detail.casefold()
