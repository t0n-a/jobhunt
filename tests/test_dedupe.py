import pytest
from sqlmodel import Session

from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.dedupe import is_duplicate, normalize
from jobhunt.jobs.models import Job, RawPosting, posting_hash
from jobhunt.jobs.store import JobStore


@pytest.fixture
def store(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as session:
        yield JobStore(session)


def make_posting(**over) -> RawPosting:
    d = {
        "source": "greenhouse",
        "external_id": "1",
        "url": "https://example.com/1",
        "title": "Senior Backend Engineer (m/f)",
        "company": "Acme Inc.",
        "description": "Build APIs.",
        "content_hash": posting_hash("Senior Backend Engineer (m/f)", "Acme Inc.", "Build APIs."),
    }
    d.update(over)
    return RawPosting(**d)


def make_job(**over) -> Job:
    d = {
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "description": "Build APIs.",
        "url": "https://other.example/9",
        "content_hash": "different",
    }
    d.update(over)
    return Job(**d)


def test_normalize_strips_punctuation_case_and_legal_forms():
    assert normalize("Senior Backend Engineer (m/f)") == "senior backend engineer"
    assert normalize("Acme Inc.") == "acme"
    assert normalize("ACME  Lda") == "acme"
    assert normalize("Acme GmbH") == "acme"


def test_normalize_handles_multi_marker_gender_suffixes():
    assert normalize("Backend Engineer (m/w/d)") == "backend engineer"
    assert normalize("Backend Engineer (h/f)") == "backend engineer"


def test_normalize_is_safe_on_empty_input():
    assert normalize("") == ""
    assert normalize(None) == ""


def test_is_duplicate_matches_across_cosmetic_differences():
    assert is_duplicate(make_posting(), make_job()) is True


def test_is_duplicate_rejects_a_different_role_at_the_same_company():
    assert is_duplicate(make_posting(title="Data Engineer"), make_job()) is False


def test_is_duplicate_rejects_the_same_role_at_a_different_company():
    assert is_duplicate(make_posting(company="Globex"), make_job()) is False


def test_is_duplicate_keeps_seniority_distinct():
    """Backend Engineer and Senior Backend Engineer are different openings the
    user may want to judge separately, so dedupe must not collapse them."""
    posting = make_posting(title="Backend Engineer", company="Acme")
    assert is_duplicate(posting, make_job(title="Senior Backend Engineer")) is False


def test_upsert_creates_a_job_and_links_the_posting(store: JobStore):
    posting = make_posting()
    job = store.upsert_posting(posting)

    assert job.id is not None
    assert posting.job_id == job.id
    assert len(store.list_jobs()) == 1


def test_upsert_collapses_the_same_role_from_two_sources(store: JobStore):
    store.upsert_posting(make_posting(source="greenhouse", external_id="1"))
    store.upsert_posting(
        make_posting(source="remotive", external_id="99", title="Senior Backend Engineer")
    )

    assert len(store.list_jobs()) == 1


def test_upsert_updates_last_seen_on_a_repeat(store: JobStore):
    first = store.upsert_posting(make_posting())
    original = first.last_seen

    second = store.upsert_posting(make_posting(external_id="2"))

    assert second.id == first.id
    assert second.last_seen >= original


def test_upsert_keeps_the_longest_description(store: JobStore):
    """A truncated aggregator copy must not overwrite the full ATS text — the
    scorer's judgement is only as good as the description it is given."""
    store.upsert_posting(make_posting(description="Short."))
    job = store.upsert_posting(
        make_posting(external_id="2", description="A much longer and fuller description.")
    )

    assert job.description == "A much longer and fuller description."


def test_upsert_does_not_shorten_an_existing_description(store: JobStore):
    store.upsert_posting(make_posting(description="A much longer and fuller description."))
    job = store.upsert_posting(make_posting(external_id="2", description="Short."))

    assert job.description == "A much longer and fuller description."


def test_remote_is_sticky_across_sources(store: JobStore):
    """One board may omit the remote flag the other sets; remote anywhere wins."""
    store.upsert_posting(make_posting(remote=False))
    job = store.upsert_posting(make_posting(external_id="2", remote=True))

    assert job.remote is True


def test_add_manual_creates_a_job(store: JobStore):
    job = store.add_manual("Platform Engineer", "Acme", "Run things", "https://x/1")

    assert job.id is not None
    assert store.get(job.id).title == "Platform Engineer"


def test_get_unknown_id_returns_none(store: JobStore):
    assert store.get(99999) is None


def test_list_jobs_respects_the_limit(store: JobStore):
    store.upsert_posting(make_posting(title="A", company="One"))
    store.upsert_posting(make_posting(title="B", company="Two"))

    assert len(store.list_jobs(limit=1)) == 1
