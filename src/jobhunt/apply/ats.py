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
# Slugs go into API URLs and a browser address: letters, digits, "-" and "_" only.
_SLUG = r"[A-Za-z0-9_-]+"
_GREENHOUSE_PATH = re.compile(rf"/({_SLUG})/jobs/([0-9]+)/?")
_LEVER_PATH = re.compile(rf"/({_SLUG})/({_UUID})(?:/apply)?/?")
_ASHBY_PATH = re.compile(rf"/({_SLUG})/({_UUID})(?:/application)?/?")


@dataclass(frozen=True)
class AtsTarget:
    kind: str
    board: str
    job_id: str
    apply_url: str


def detect_ats(url: str) -> AtsTarget | None:
    # A browser reads "https://evil\\@ats-host/..." as evil: the form (and the candidate's
    # details) must only ever go to the host we checked.
    if not url or "\\" in url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return None
    host = parts.hostname or ""
    path = parts.path

    if host in _GREENHOUSE_HOSTS:
        host = "job-boards.greenhouse.io" if host == "boards.greenhouse.io" else host  # it 301s there
        m = _GREENHOUSE_PATH.fullmatch(path)
        if m:
            return AtsTarget("greenhouse", m[1], m[2], f"https://{host}/{m[1]}/jobs/{m[2]}")
        if path.rstrip("/") == "/embed/job_app":
            query = parse_qs(parts.query)
            board = query.get("for", [""])[0]
            token = query.get("token", [""])[0]
            if re.fullmatch(_SLUG, board) and token.isascii() and token.isdigit():
                return AtsTarget(
                    "greenhouse", board, token, f"https://{host}/{board}/jobs/{token}"
                )
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
