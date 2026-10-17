"""Recognise the ATS behind a job URL from its path (Greenhouse, Lever, Ashby)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

_GREENHOUSE_HOSTS = frozenset(
    {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}
)
_LEVER_HOSTS = frozenset({"jobs.lever.co", "jobs.eu.lever.co"})
_ASHBY_HOST = "jobs.ashbyhq.com"

# Board and job slugs are free text; the Lever and Ashby ids are plain UUIDs.
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_GREENHOUSE_PATH = re.compile(r"/([^/]+)/jobs/([0-9]+)/?")
_LEVER_PATH = re.compile(rf"/([^/]+)/({_UUID})(?:/apply)?/?")
_ASHBY_PATH = re.compile(rf"/([^/]+)/({_UUID})(?:/application)?/?")


@dataclass(frozen=True)
class AtsTarget:
    kind: str
    board: str
    job_id: str
    apply_url: str


def detect_ats(url: str) -> AtsTarget | None:
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https"):
        return None
    host = parts.hostname or ""
    path = parts.path

    if host in _GREENHOUSE_HOSTS:
        m = _GREENHOUSE_PATH.fullmatch(path)
        if m:
            return AtsTarget("greenhouse", m[1], m[2], url)
        if path.rstrip("/") == "/embed/job_app":
            query = parse_qs(parts.query)
            board = query.get("for", [""])[0]
            token = query.get("token", [""])[0]
            if board and token.isascii() and token.isdigit():
                return AtsTarget("greenhouse", board, token, url)
        return None

    if host in _LEVER_HOSTS:
        m = _LEVER_PATH.fullmatch(path)
        if m:
            return AtsTarget("lever", m[1], m[2], f"https://{host}/{m[1]}/{m[2]}/apply")
        return None

    if host == _ASHBY_HOST:
        m = _ASHBY_PATH.fullmatch(path)
        if m:
            return AtsTarget("ashby", m[1], m[2], f"https://{host}/{m[1]}/{m[2]}/application")
    return None
