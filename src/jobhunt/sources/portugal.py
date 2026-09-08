import logging
from collections.abc import Iterable

import httpx

from jobhunt.jobs.models import RawPosting, posting_hash
from jobhunt.sources.ats import TIMEOUT, parse_iso, strip_html
from jobhunt.sources.base import register

logger = logging.getLogger(__name__)

LANDING_URL = "https://landing.jobs/api/v1/jobs?limit=200"
ITJOBS_URL = "https://api.itjobs.pt/job/list.json"


def _fetch_json(url: str, params: dict | None = None):
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "jobhunt"}) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("source fetch failed for %s: %s", url, exc)
        return None


@register
class LandingJobsSource:
    name = "landingjobs"

    def fetch(self) -> Iterable[RawPosting]:
        payload = _fetch_json(LANDING_URL)
        if not payload or not isinstance(payload, list):
            return
        for item in payload:
            try:
                title = item["title"]
                # Landing.jobs exposes no company field on this endpoint.
                company = item.get("company_name") or "unknown"
                location = _first_location(item.get("locations") or [])
                description = " ".join(
                    strip_html(item.get(field, ""))
                    for field in ("role_description", "main_requirements", "nice_to_have")
                ).strip()
                tags = item.get("tags") or []
                if tags:
                    description = f"{description} Skills: {', '.join(tags)}."
                yield RawPosting(
                    source=self.name,
                    external_id=str(item["id"]),
                    url=item.get("url", ""),
                    title=title,
                    company=company,
                    location=location,
                    remote=bool(item.get("remote")),
                    description=description,
                    posted_at=parse_iso(item.get("published_at")),
                    content_hash=posting_hash(title, company, description),
                )
            except (KeyError, TypeError) as exc:
                logger.warning("%s: skipping malformed item: %s", self.name, exc)


@register
class ITJobsSource:
    """Portugal's largest IT board. Requires a free API key from itjobs.pt/api."""

    name = "itjobs"

    def __init__(self, api_key: str | None):
        self.api_key = api_key

    def fetch(self) -> Iterable[RawPosting]:
        if not self.api_key:
            logger.info("itjobs: no API key configured, skipping")
            return
        payload = _fetch_json(ITJOBS_URL, {"api_key": self.api_key, "limit": 200})
        if not payload or "results" not in payload:
            if payload and "error" in payload:
                logger.warning("itjobs: %s", payload["error"].get("message"))
            return
        for item in payload["results"]:
            try:
                title = item["title"]
                company = (item.get("company") or {}).get("name") or "unknown"
                locations = item.get("locations") or []
                location = (
                    locations[0].get("name")
                    if locations and isinstance(locations[0], dict)
                    else None
                )
                description = strip_html(item.get("body", ""))
                yield RawPosting(
                    source=self.name,
                    external_id=str(item["id"]),
                    url=item.get("url") or f"https://www.itjobs.pt/oferta/{item['id']}",
                    title=title,
                    company=company,
                    location=location,
                    remote=False,
                    description=description,
                    posted_at=None,
                    content_hash=posting_hash(title, company, description),
                )
            except (KeyError, TypeError) as exc:
                logger.warning("%s: skipping malformed item: %s", self.name, exc)


def _first_location(locations: list[dict]) -> str | None:
    if not locations or not isinstance(locations[0], dict):
        return None
    first = locations[0]
    city = first.get("city")
    country = first.get("country_code")
    return ", ".join(part for part in (city, country) if part) or None
