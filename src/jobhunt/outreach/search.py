"""Public web search (Tavily) for a company's recruiters. Only LinkedIn /in/ results that name the company."""

import logging
import re

import httpx

from jobhunt.jobs.dedupe import normalize
from jobhunt.outreach.contacts import _RECRUITER, ContactCandidate, _not_person
from jobhunt.ranking.shortlist import _clean

TAVILY_URL = "https://api.tavily.com/search"
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines carry the request URL
log = logging.getLogger(__name__)

_PROFILE = re.compile(r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([A-Za-z0-9_%-]+)", re.IGNORECASE)
_SEP = re.compile(r"\s+[-–|]\s+")
_NAME = re.compile(r"^[A-ZÀ-Ý][a-zà-ÿ'’-]+ [A-ZÀ-Ý][a-zà-ÿ'’-]+$")
_MAX_ROLE = 60
_MAX_CANDIDATES = 3


def _candidate(item: dict, company: str) -> ContactCandidate | None:
    url = _PROFILE.match(str(item.get("url") or ""))
    title = _clean(str(item.get("title") or ""))
    if not url or not title:
        return None
    parts = _SEP.split(title)
    name = parts[0].strip()
    if not _NAME.match(name) or _not_person(name):
        return None
    text = f"{title} {_clean(str(item.get('content') or ''))}"
    # Whole words: "Acme" must not match a recruiter at "Acmeville Corp".
    company_words = re.escape(normalize(company))
    if not re.search(rf"\b{company_words}\b", normalize(text)) or not _RECRUITER.search(text):
        return None
    role = next((p.strip()[:_MAX_ROLE] for p in parts[1:] if _RECRUITER.search(p)), None)
    return ContactCandidate(name, role, f"https://www.linkedin.com/in/{url.group(1)}", None, "search")


def search_recruiters(company: str, *, api_key: str, http: httpx.Client) -> list[ContactCandidate]:
    body = {
        "query": f'"{company}" recruiter OR "talent acquisition" site:linkedin.com/in',
        "max_results": 5,
        "search_depth": "basic",
    }
    try:
        resp = http.post(TAVILY_URL, json=body, headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        if resp.status_code != 200:
            log.warning("recruiter search failed: HTTP %s", resp.status_code)
            return []
        results = resp.json()["results"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("recruiter search failed: %s", type(e).__name__)
        return []
    found: dict[str, ContactCandidate] = {}
    for item in results if isinstance(results, list) else []:
        if isinstance(item, dict) and (c := _candidate(item, company)):
            found.setdefault(c.profile_url, c)
    return list(found.values())[:_MAX_CANDIDATES]
