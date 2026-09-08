from datetime import datetime

import pytest
from sqlmodel import Session

from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, RawPosting, posting_hash
from jobhunt.sources.base import SourceError, register, registry


def test_posting_hash_is_stable_and_content_sensitive():
    a = posting_hash("Backend Engineer", "Acme", "Build APIs.")
    b = posting_hash("Backend Engineer", "Acme", "Build APIs.")
    c = posting_hash("Backend Engineer", "Acme", "Build other APIs.")

    assert a == b
    assert a != c


def test_posting_hash_ignores_case_and_surrounding_whitespace():
    assert posting_hash("Backend Engineer", "Acme", "X") == posting_hash(
        "  backend engineer ", " ACME ", "X"
    )


def test_raw_posting_defaults():
    p = RawPosting(
        source="greenhouse",
        external_id="1",
        url="https://example.com/1",
        title="Backend Engineer",
        company="Acme",
        description="Build APIs.",
        content_hash="abc",
    )

    assert p.remote is False
    assert p.job_id is None
    assert isinstance(p.fetched_at, datetime)


def test_register_adds_to_registry():
    @register
    class Dummy:
        name = "dummy"

        def fetch(self):
            return []

    assert registry["dummy"] is Dummy


def test_source_error_is_an_exception():
    with pytest.raises(SourceError):
        raise SourceError("boom")


def test_job_and_raw_posting_tables_create_together(tmp_path):
    """RawPosting.job_id is a foreign key to job.id, so both models must be
    registered before create_all can emit the DDL."""
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as session:
        job = Job(title="T", company="C", description="D", url="u", content_hash="h")
        session.add(job)
        session.commit()
        session.refresh(job)
        assert job.id is not None
