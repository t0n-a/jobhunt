import logging
import random
from collections.abc import Callable, Iterable

import pandas as pd

from jobhunt.config import JobSpyConfig
from jobhunt.jobs.models import RawPosting, posting_hash, truncate_description
from jobhunt.sources.base import SourceError

logger = logging.getLogger(__name__)


def _val(row, key):
    """Row value, with NaN/None/missing all read as None."""
    v = row.get(key)
    return None if v is None or pd.isna(v) else v


class JobSpySource:
    """LinkedIn (logged-out, best-effort) and Indeed through the JobSpy scraper."""

    def __init__(self, site: str, cfg: JobSpyConfig, scrape: Callable | None = None, shuffle=random.shuffle):
        self.name = site
        self.cfg = cfg
        self.deadline_seconds = cfg.deadline_seconds
        self._scrape = scrape
        self._shuffle = shuffle

    def fetch(self) -> Iterable[RawPosting]:
        scrape = self._scrape
        if scrape is None:
            from jobspy import scrape_jobs as scrape  # lazy: keeps tests off the network
        got_rows = False
        pairs = [(t, loc) for t in self.cfg.search_terms for loc in self.cfg.locations]
        # LinkedIn's 3-7 s page delay needs the longer per-source deadline.
        self._shuffle(pairs)
        for term, loc in pairs:
            try:
                df = scrape(
                    site_name=[self.name],
                    search_term=term,
                    location=loc,
                    results_wanted=self.cfg.results_wanted,
                    hours_old=self.cfg.hours_old,
                    country_indeed=self.cfg.country_indeed,
                    fetch_description=True,
                    enforce_annual_salary=True,
                    verbose=0,
                )
            except Exception as exc:  # noqa: BLE001 - scraper failures are many and untyped
                logger.warning("%s: scrape %r/%r failed: %s", self.name, term, loc, exc)
                continue
            if df is None or df.empty:
                continue
            got_rows = True
            for row in df.to_dict("records"):
                try:
                    posting = self._to_posting(row)
                except Exception as exc:  # noqa: BLE001 - one malformed row must not sink the source
                    logger.warning("%s: skipping malformed row: %s", self.name, exc)
                    continue
                if posting:
                    yield posting
        if not got_rows:
            raise SourceError(f"{self.name} returned nothing; likely blocked or rate-limited")

    def _to_posting(self, row: dict) -> RawPosting | None:
        title, company, url = _val(row, "title"), _val(row, "company"), _val(row, "job_url")
        if not (title and company and url):
            return None
        lo, hi = _val(row, "min_amount"), _val(row, "max_amount")
        salary = None
        if lo is not None:
            top = hi if hi is not None else lo
            salary = f"{_val(row, 'currency') or ''} {lo:g}-{top:g} yearly".strip()
        description = _val(row, "description") or ""
        posted = _val(row, "date_posted")
        return RawPosting(
            source=self.name,
            external_id=str(row["id"]),
            url=url,
            title=title,
            company=company,
            location=_val(row, "location"),
            remote=bool(_val(row, "is_remote")),
            description=truncate_description(description),
            posted_at=pd.Timestamp(posted).to_pydatetime() if posted is not None else None,
            salary_text=salary,
            content_hash=posting_hash(title, company, description),
        )
