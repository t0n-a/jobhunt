"""Thin wrapper over the `gh` CLI. Every call goes through `run`, so tests never reach GitHub."""

import base64
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?$")
_VISIBILITY = {"public", "private", "internal"}
_TIMEOUT = 120
_TREE_LIMIT = 500
_REPO_FIELDS = (
    "name,nameWithOwner,description,isFork,isEmpty,pushedAt,primaryLanguage,stargazerCount,diskUsage"
)


class GhError(RuntimeError):
    pass


def _name(value: str) -> str:
    # The pattern alone admits "--help"; a leading dash would be read as a gh flag.
    if not _NAME.fullmatch(value) or value.startswith("-"):
        raise GhError("bad repository name")
    return value


class Gh:
    def __init__(self, run: Callable = subprocess.run):
        self._run = run

    def _exec(self, args: list[str]):
        try:
            return self._run(["gh", *args], capture_output=True, text=True, timeout=_TIMEOUT)
        except FileNotFoundError as e:
            raise GhError("gh is not installed") from e
        except subprocess.TimeoutExpired as e:
            raise GhError("gh timed out") from e

    def _ok(self, args: list[str]) -> str:
        proc = self._exec(args)
        if proc.returncode != 0:
            raise GhError(proc.stderr.strip()[:300])
        return proc.stdout

    def repos(self, user: str, visibility: str) -> list[dict]:
        if visibility not in _VISIBILITY:
            raise GhError("bad visibility")
        out = self._ok(
            [
                "repo", "list", _name(user),
                "--visibility", visibility,
                "--limit", "200",
                "--json", _REPO_FIELDS,
            ]
        )
        return self._json(out)

    @staticmethod
    def _json(text: str):
        try:
            return json.loads(text)
        except ValueError as e:  # malformed gh output must not escape as a non-GhError
            raise GhError(f"unexpected gh output: {e}") from e

    def languages(self, full_name: str) -> dict[str, int]:
        return self._json(self._ok(["api", f"repos/{_name(full_name)}/languages"]))

    def readme(self, full_name: str) -> tuple[str, str | None]:
        proc = self._exec(["api", f"repos/{_name(full_name)}/readme"])
        if proc.returncode != 0:
            if "HTTP 404" in proc.stderr:
                return "", None
            raise GhError(proc.stderr.strip()[:300])
        data = self._json(proc.stdout)
        try:
            # GitHub wraps the base64 body at 60 columns; b64decode skips the newlines.
            text = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            return text, data["sha"]
        except (KeyError, TypeError, ValueError) as e:
            raise GhError(f"unexpected README payload: {e}") from e

    def tree(self, full_name: str) -> list[str]:
        data = self._json(self._ok(["api", f"repos/{_name(full_name)}/git/trees/HEAD?recursive=1"]))
        return [entry["path"] for entry in data.get("tree", [])][:_TREE_LIMIT]

    def make_public(self, full_name: str) -> None:
        self._ok(
            [
                "repo", "edit", _name(full_name),
                "--visibility", "public",
                "--accept-visibility-change-consequences",
            ]
        )

    def put_file(
        self, full_name: str, path: str, text: str, message: str, sha: str | None
    ) -> None:
        content = base64.b64encode(text.encode("utf-8")).decode("ascii")
        args = [
            "api", "-X", "PUT", f"repos/{_name(full_name)}/contents/{path}",
            "-f", f"message={message}",
            "-f", f"content={content}",
        ]
        if sha:
            args += ["-f", f"sha={sha}"]
        self._ok(args)

    def clone_mirror(self, full_name: str, dest: Path) -> Path:
        self._ok(["repo", "clone", _name(full_name), str(dest), "--", "--mirror", "--quiet"])
        return dest
