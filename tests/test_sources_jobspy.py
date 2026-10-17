import math

import pandas as pd
import pytest

from jobhunt.config import JobSpyConfig
from jobhunt.sources.base import SourceError
from jobhunt.sources.jobspy_source import JobSpySource

ROW = dict(  # noqa: C408 - brief verbatim
    id="li-1", job_url="https://www.linkedin.com/jobs/view/1", title="Junior Developer",
           company="Acme", location="Lisbon, Portugal", is_remote=False, description="Python.",
           date_posted=None, min_amount=30000.0, max_amount=40000.0, currency="EUR")
CFG = JobSpyConfig(search_terms=["dev"], locations=["Portugal"])

def test_maps_rows_to_postings():
    src = JobSpySource("linkedin", CFG, scrape=lambda **kw: pd.DataFrame([ROW]))
    [p] = list(src.fetch())
    assert (p.source, p.title, p.company, p.salary_text) == ("linkedin", "Junior Developer", "Acme", "EUR 30000-40000 yearly")

def test_nan_salary_and_missing_fields():
    rows = [dict(ROW, min_amount=math.nan, max_amount=math.nan, currency=None), dict(ROW, id="2", title=None)]
    [p] = list(JobSpySource("indeed", CFG, scrape=lambda **kw: pd.DataFrame(rows)).fetch())
    assert p.salary_text is None

def test_empty_result_raises_source_error():
    with pytest.raises(SourceError):
        list(JobSpySource("linkedin", CFG, scrape=lambda **kw: pd.DataFrame()).fetch())

def test_one_failing_call_does_not_lose_the_others():
    calls = iter([RuntimeError("429"), pd.DataFrame([ROW])])
    def scrape(**kw):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r
    cfg = JobSpyConfig(search_terms=["a", "b"], locations=["Portugal"])
    assert len(list(JobSpySource("linkedin", cfg, scrape=scrape).fetch())) == 1


def test_one_malformed_row_is_skipped_not_the_source():
    rows = [dict(ROW, id="bad", date_posted="not a date"), dict(ROW, id="ok")]
    out = list(JobSpySource("indeed", CFG, scrape=lambda **kw: pd.DataFrame(rows)).fetch())
    assert [p.external_id for p in out] == ["ok"]
