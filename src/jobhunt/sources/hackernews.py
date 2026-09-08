import logging
from collections.abc import Iterable

import httpx

from jobhunt.jobs.models import RawPosting, posting_hash
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

    def __init__(self, keywords: list[str]):
        self.keywords = [k.casefold() for k in keywords]

    def fetch(self) -> Iterable[RawPosting]:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "jobhunt"}) as client:
            thread_id = self._latest_thread(client)
            if thread_id is None:
                return
            story = self._json(client, ITEM_URL.format(id=thread_id))
            if not story:
                return
            for comment_id in (story.get("kids") or [])[:MAX_COMMENTS]:
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
                        description=text,
                        posted_at=None,
                        content_hash=posting_hash(title, company, text),
                    )
                except (KeyError, TypeError) as exc:
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
        lowered = text.casefold()
        return any(k in lowered for k in self.keywords)

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
