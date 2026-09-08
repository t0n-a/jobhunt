import logging
from collections.abc import Iterable
from datetime import datetime

import feedparser
import httpx

from jobhunt.jobs.models import RawPosting, posting_hash
from jobhunt.sources.ats import TIMEOUT, strip_html
from jobhunt.sources.base import register

logger = logging.getLogger(__name__)

REMOTIVE_URL = "https://remotive.com/api/remote-jobs?limit=200"
REMOTEOK_URL = "https://remoteok.com/api"
WWR_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"


def _fetch_json(url: str):
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "jobhunt"}) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("source fetch failed for %s: %s", url, exc)
        return None


@register
class RemotiveSource:
    name = "remotive"

    def fetch(self) -> Iterable[RawPosting]:
        payload = _fetch_json(REMOTIVE_URL)
        if not payload:
            return
        for item in payload.get("jobs", []):
            try:
                title = item["title"]
                company = item.get("company_name") or "unknown"
                description = strip_html(item.get("description", ""))
                yield RawPosting(
                    source=self.name,
                    external_id=str(item["id"]),
                    url=item.get("url", ""),
                    title=title,
                    company=company,
                    location=item.get("candidate_required_location"),
                    remote=True,
                    description=description,
                    posted_at=_parse(item.get("publication_date")),
                    content_hash=posting_hash(title, company, description),
                )
            except (KeyError, TypeError) as exc:
                logger.warning("%s: skipping malformed item: %s", self.name, exc)


@register
class RemoteOkSource:
    name = "remoteok"

    def fetch(self) -> Iterable[RawPosting]:
        payload = _fetch_json(REMOTEOK_URL)
        if not payload:
            return
        for item in payload:
            # The API's first element is a legal/metadata object, not a job.
            if not isinstance(item, dict) or "position" not in item:
                continue
            try:
                title = item["position"]
                company = item.get("company") or "unknown"
                description = strip_html(item.get("description", ""))
                yield RawPosting(
                    source=self.name,
                    external_id=str(item["id"]),
                    url=item.get("url", ""),
                    title=title,
                    company=company,
                    location=item.get("location"),
                    remote=True,
                    description=description,
                    posted_at=_parse(item.get("date")),
                    content_hash=posting_hash(title, company, description),
                )
            except (KeyError, TypeError) as exc:
                logger.warning("%s: skipping malformed item: %s", self.name, exc)


@register
class WeWorkRemotelySource:
    name = "weworkremotely"

    def fetch(self) -> Iterable[RawPosting]:
        try:
            with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "jobhunt"}) as client:
                response = client.get(WWR_URL)
                response.raise_for_status()
                raw = response.text
        except httpx.HTTPError as exc:
            logger.warning("source fetch failed for %s: %s", WWR_URL, exc)
            return

        for entry in feedparser.parse(raw).entries:
            try:
                # WWR titles are "Company: Role".
                raw_title = entry.get("title", "")
                company, _, role = raw_title.partition(":")
                title = (role or raw_title).strip()
                company = company.strip() if role else "unknown"
                description = strip_html(entry.get("description", ""))
                yield RawPosting(
                    source=self.name,
                    external_id=entry.get("link", raw_title),
                    url=entry.get("link", ""),
                    title=title,
                    company=company,
                    location=entry.get("region"),
                    remote=True,
                    description=description,
                    posted_at=None,
                    content_hash=posting_hash(title, company, description),
                )
            except (KeyError, TypeError) as exc:
                logger.warning("%s: skipping malformed entry: %s", self.name, exc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
