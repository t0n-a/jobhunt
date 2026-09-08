import logging

import httpx

from jobhunt.sources.ats import TIMEOUT

logger = logging.getLogger(__name__)

PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


def probe_board(client: httpx.Client, kind: str, slug: str) -> bool:
    """True when this slug is a real board on this ATS with at least one job."""
    try:
        response = client.get(PROBES[kind].format(slug=slug))
        if response.status_code != 200:
            return False
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("probe failed for %s/%s: %s", kind, slug, exc)
        return False

    jobs = payload if isinstance(payload, list) else payload.get("jobs", [])
    return bool(jobs)


def discover_boards(slugs: list[str]) -> dict[str, list[str]]:
    """Probe candidate company slugs against all three ATS APIs.

    A company's board slug is rarely documented anywhere, so the cheapest way to
    find it is to ask each ATS whether it exists. Only slugs that answer with at
    least one job are kept.
    """
    found: dict[str, list[str]] = {kind: [] for kind in PROBES}
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "jobhunt"}) as client:
        for slug in slugs:
            for kind in PROBES:
                if probe_board(client, kind, slug):
                    found[kind].append(slug)
    return found
