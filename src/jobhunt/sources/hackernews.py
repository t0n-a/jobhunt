import logging
import time
from collections.abc import Iterable

import httpx

from jobhunt.jobs.models import RawPosting, posting_hash, truncate_description
from jobhunt.sources.ats import TIMEOUT, strip_html
from jobhunt.sources.base import register

logger = logging.getLogger(__name__)

# Only this query returns the CURRENT thread; a plain title search returns
# threads from 2016.
SEARCH_URL = (
    "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=5"
)
ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
MAX_COMMENTS = 400


@register
class HackerNewsSource:
    """Top-level comments on the monthly "Who is hiring?" thread."""

    name = "hackernews"

    def __init__(
        self,
        keywords: list[str],
        role_keywords: list[str] | None = None,
        deadline_seconds: float = 120.0,
    ):
        self.keywords = [k.casefold() for k in keywords]
        # A live scan showed a location keyword alone matches every remote job of
        # any kind — Sales, Copywriting and Freelance Writing all came through on
        # "remote". A posting must also read as engineering work.
        self.role_keywords = [k.casefold() for k in (role_keywords or [])]
        # This adapter walks up to MAX_COMMENTS comments, one HTTP call each, and
        # once role-filtered most of them yield nothing. The scan's own deadline
        # cannot help: all that work happens inside a single next(), which the
        # consuming loop has no way to interrupt. So bound it here, at the source.
        self.deadline_seconds = deadline_seconds

    def fetch(self) -> Iterable[RawPosting]:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "jobhunt"}) as client:
            thread_id = self._latest_thread(client)
            if thread_id is None:
                return
            story = self._json(client, ITEM_URL.format(id=thread_id))
            if not story:
                return
            started = time.monotonic()
            for comment_id in (story.get("kids") or [])[:MAX_COMMENTS]:
                # Inclusive on purpose. `>` only stops a zero budget if the clock
                # is finer-grained than the work: time.monotonic() resolves to
                # 15.6ms on Windows, so 40 mocked fetches all report elapsed as
                # exactly 0.0, and `0.0 > 0` let every one of them through.
                if time.monotonic() - started >= self.deadline_seconds:
                    logger.warning(
                        "hackernews: stopped at the %ss deadline; "
                        "the thread has more comments than that budget allows",
                        self.deadline_seconds,
                    )
                    return
                try:
                    comment = self._json(client, ITEM_URL.format(id=comment_id))
                    if not comment or comment.get("deleted") or comment.get("dead"):
                        continue
                    text = strip_html(comment.get("text", ""))
                    if not text or not self._matches(text):
                        continue
                    title, company = _split_heading(text)
                    yield RawPosting(
                        source=self.name,
                        external_id=str(comment["id"]),
                        url=f"https://news.ycombinator.com/item?id={comment['id']}",
                        title=title,
                        company=company,
                        location=None,
                        remote="remote" in text.casefold(),
                        description=truncate_description(text),
                        posted_at=None,
                        content_hash=posting_hash(title, company, text),
                    )
                except (KeyError, TypeError, AttributeError) as exc:
                    logger.warning("%s: skipping malformed item: %s", self.name, exc)

    def _latest_thread(self, client: httpx.Client) -> str | None:
        payload = self._json(client, SEARCH_URL)
        if not payload:
            return None
        for hit in payload.get("hits", []):
            if "who is hiring" in hit.get("title", "").casefold():
                return hit["objectID"]
        return None

    def _matches(self, text: str) -> bool:
        """A posting must match a location keyword AND look like engineering work.

        Location alone is far too loose on free prose: "remote" appears in every
        remote listing regardless of discipline.
        """
        lowered = text.casefold()
        if not any(k in lowered for k in self.keywords):
            return False
        if not self.role_keywords:
            return True
        return any(k in lowered for k in self.role_keywords)

    def _json(self, client: httpx.Client, url: str):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("hackernews fetch failed for %s: %s", url, exc)
            return None


def _split_heading(text: str) -> tuple[str, str]:
    """HN hiring posts conventionally start "Company | Location | Role"."""
    head = text.split("\n", 1)[0]
    parts = [p.strip() for p in head.split("|") if p.strip()]
    company = parts[0] if parts else "unknown"
    title = parts[2] if len(parts) > 2 else (parts[-1] if parts else head[:80])
    return title[:200], company[:200]
