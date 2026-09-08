import html
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime

import httpx

from jobhunt.jobs.models import RawPosting, posting_hash
from jobhunt.sources.base import register

logger = logging.getLogger(__name__)

TIMEOUT = 30
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Word-boundary matched so "Remoteness, Alaska" is not a remote job.
_REMOTE_RE = re.compile(r"\b(remote|anywhere|worldwide|distributed)\b", re.IGNORECASE)
# A posting that names an office or a hybrid arrangement is not fully remote,
# even when it also says "remote-friendly".
_ONSITE_RE = re.compile(r"\b(on[- ]?site|hybrid|in[- ]office)\b", re.IGNORECASE)


def strip_html(raw: str) -> str:
    """Turn an HTML description into readable plain text."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text).replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def _is_remote(location: str | None) -> bool:
    """True only for genuinely remote locations.

    `remote` feeds the scorer, which weighs Porto and remote equally, so a false
    positive here corrupts a scoring input rather than merely mislabelling a row.
    """
    if not location:
        return False
    return bool(_REMOTE_RE.search(location)) and not _ONSITE_RE.search(location)


def _get(client: httpx.Client, url: str) -> dict | list | None:
    """Fetch one board. Returns None on failure so the caller can continue."""
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("source fetch failed for %s: %s", url, exc)
        return None


class _AtsSource:
    """Shared shape: iterate boards, skip failures, yield postings."""

    name = "ats"

    def __init__(self, boards: list[str]):
        self.boards = boards

    def fetch(self) -> Iterable[RawPosting]:
        if not self.boards:
            return
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "jobhunt"}) as client:
            for board in self.boards:
                payload = _get(client, self.url_for(board))
                if payload is None:
                    continue
                for item in self.items(payload):
                    try:
                        yield self.to_posting(board, item)
                    except (KeyError, TypeError) as exc:
                        logger.warning("%s: skipping malformed item: %s", self.name, exc)

    def url_for(self, board: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def items(self, payload) -> list:  # pragma: no cover - overridden
        raise NotImplementedError

    def to_posting(self, board: str, item: dict) -> RawPosting:  # pragma: no cover
        raise NotImplementedError


@register
class GreenhouseSource(_AtsSource):
    name = "greenhouse"

    def url_for(self, board: str) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"

    def items(self, payload) -> list:
        return payload.get("jobs", [])

    def to_posting(self, board: str, item: dict) -> RawPosting:
        location = (item.get("location") or {}).get("name")
        description = strip_html(item.get("content", ""))
        title = item["title"]
        return RawPosting(
            source=self.name,
            external_id=str(item["id"]),
            url=item.get("absolute_url", ""),
            title=title,
            company=board,
            location=location,
            remote=_is_remote(location),
            description=description,
            posted_at=parse_iso(item.get("updated_at")),
            content_hash=posting_hash(title, board, description),
        )


@register
class LeverSource(_AtsSource):
    name = "lever"

    def url_for(self, board: str) -> str:
        return f"https://api.lever.co/v0/postings/{board}?mode=json"

    def items(self, payload) -> list:
        return payload if isinstance(payload, list) else []

    def to_posting(self, board: str, item: dict) -> RawPosting:
        location = (item.get("categories") or {}).get("location")
        description = item.get("descriptionPlain") or strip_html(item.get("description", ""))
        title = item["text"]
        return RawPosting(
            source=self.name,
            external_id=str(item["id"]),
            url=item.get("hostedUrl", ""),
            title=title,
            company=board,
            location=location,
            remote=_is_remote(location),
            description=description,
            posted_at=_parse_epoch_ms(item.get("createdAt")),
            content_hash=posting_hash(title, board, description),
        )


@register
class AshbySource(_AtsSource):
    name = "ashby"

    def url_for(self, board: str) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{board}"

    def items(self, payload) -> list:
        return payload.get("jobs", [])

    def to_posting(self, board: str, item: dict) -> RawPosting:
        location = item.get("location")
        description = item.get("descriptionPlain") or strip_html(item.get("descriptionHtml", ""))
        title = item["title"]
        return RawPosting(
            source=self.name,
            external_id=str(item["id"]),
            url=item.get("jobUrl", ""),
            title=title,
            company=board,
            location=location,
            remote=bool(item.get("isRemote")) or _is_remote(location),
            description=description,
            posted_at=parse_iso(item.get("publishedAt")),
            content_hash=posting_hash(title, board, description),
        )


def parse_iso(value: str | None) -> datetime | None:
    """Shared ISO-8601 parser. Imported by the other source modules."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# Milliseconds since 1970 for any plausible posting date exceed 10^12. A value
# below that is a seconds-based epoch, which divided by 1000 would silently
# yield a date near 1970 instead of failing.
_MIN_EPOCH_MS = 10**12


def _parse_epoch_ms(value: int | None) -> datetime | None:
    if not value or not isinstance(value, int) or value < _MIN_EPOCH_MS:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, TypeError):
        return None
