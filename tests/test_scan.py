from sqlmodel import Session, select

from jobhunt.config import SourcesConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, RawPosting, Score, posting_hash
from jobhunt.llm import FakeProvider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore
from jobhunt.scan import ScanResult, build_sources, run_scan


class StubSource:
    def __init__(self, name, postings=None, error=None, delay=0.0):
        self.name = name
        self._postings = postings or []
        self._error = error
        self._delay = delay

    def fetch(self):
        if self._error:
            raise self._error
        for posting in self._postings:
            if self._delay:
                import time

                time.sleep(self._delay)
            yield posting


def posting(title="Backend Engineer", company="Acme", description="Python and Redis."):
    slug = f"{company}/{title}".lower().replace(" ", "-")
    return RawPosting(
        source="stub",
        external_id="1",
        url=f"https://example.com/{slug}",
        title=title,
        company=company,
        description=description,
        content_hash=posting_hash(title, company, description),
    )


def score_payload(**over):
    payload = {
        "fit_score": 70,
        "matched_skills": ["Python"],
        "missing_skills": ["Kubernetes"],
        "red_flags": [],
        "seniority_fit": "under",
        "track": "backend",
        "rationale": "Good overlap.",
    }
    payload.update(over)
    return payload


def fresh(tmp_path, name="s.db", with_profile=True):
    """Scoring now refuses an empty profile, so seed one confirmed atom unless a
    test is specifically about the empty-profile path."""
    engine = create_engine_for(tmp_path / name)
    init_db(engine)
    if with_profile:
        with Session(engine) as session:
            AtomStore(session).add(
                EvidenceAtom(
                    kind=AtomKind.PROJECT,
                    title="URL shortener",
                    description="Flask and Redis.",
                    skills=["Python", "Redis"],
                    provenance=Provenance.INTERVIEW,
                    confirmed=True,
                )
            )
    return engine


def test_run_scan_fetches_dedupes_and_scores(tmp_path):
    engine = fresh(tmp_path)

    result = run_scan(engine, [StubSource("stub", [posting()])], FakeProvider([score_payload()]))

    assert result.fetched == 1
    assert result.jobs == 1
    assert result.scored == 1
    with Session(engine) as session:
        assert len(session.exec(select(Score)).all()) == 1


def test_a_failing_source_does_not_abort_the_scan(tmp_path):
    engine = fresh(tmp_path)

    result = run_scan(
        engine,
        [
            StubSource("broken", error=RuntimeError("down")),
            StubSource("working", [posting()]),
        ],
        FakeProvider([score_payload()]),
    )

    assert result.failed_sources == ["broken"]
    assert result.jobs == 1


def test_a_failing_score_leaves_the_job_unscored_not_dropped(tmp_path):
    engine = fresh(tmp_path)

    # An exhausted FakeProvider raises LLMError on every score call.
    result = run_scan(engine, [StubSource("stub", [posting()])], FakeProvider([]))

    assert result.jobs == 1
    assert result.scored == 0
    with Session(engine) as session:
        assert len(session.exec(select(Job)).all()) == 1


def test_rescanning_an_unchanged_job_does_not_score_it_again(tmp_path):
    engine = fresh(tmp_path)

    run_scan(engine, [StubSource("stub", [posting()])], FakeProvider([score_payload()]))
    second = run_scan(engine, [StubSource("stub", [posting()])], FakeProvider([]))

    assert second.scored == 0
    with Session(engine) as session:
        assert len(session.exec(select(Score)).all()) == 1


def test_a_changed_description_is_rescored(tmp_path):
    """content_hash changes when the description does, so the cached score no
    longer applies and the job must be judged again."""
    engine = fresh(tmp_path)

    run_scan(
        engine, [StubSource("s", [posting(description="Short.")])], FakeProvider([score_payload()])
    )
    second = run_scan(
        engine,
        [StubSource("s", [posting(description="A much longer description with more detail.")])],
        FakeProvider([score_payload(fit_score=90)]),
    )

    assert second.scored == 1


def test_two_sources_posting_the_same_role_produce_one_job(tmp_path):
    engine = fresh(tmp_path)

    result = run_scan(
        engine,
        [
            StubSource("a", [posting()]),
            StubSource("b", [posting(title="Backend Engineer (m/f)")]),
        ],
        FakeProvider([score_payload()]),
    )

    assert result.fetched == 2
    assert result.jobs == 1
    assert result.scored == 1


def test_a_source_that_overruns_its_deadline_keeps_what_it_collected(tmp_path):
    """Hacker News can issue hundreds of sequential fetches; one slow source must
    not consume the whole scan, but its partial results are still worth keeping."""
    engine = fresh(tmp_path)
    slow = StubSource(
        "slow",
        [posting(title="A", company="One"), posting(title="B", company="Two")],
        delay=0.05,
    )

    result = run_scan(engine, [slow], FakeProvider([score_payload()]), deadline_seconds=0.01)

    assert result.timed_out_sources == ["slow"]
    assert result.fetched == 1  # stopped after the first, kept it
    assert result.jobs == 1


def test_a_timed_out_source_does_not_block_later_sources(tmp_path):
    engine = fresh(tmp_path)

    result = run_scan(
        engine,
        [
            StubSource("slow", [posting(title="A", company="One")], delay=0.05),
            StubSource("fast", [posting(title="B", company="Two")]),
        ],
        FakeProvider([score_payload(), score_payload()]),
        deadline_seconds=0.01,
    )

    assert "slow" in result.timed_out_sources
    assert result.jobs == 2


def test_scan_with_no_sources_is_a_no_op(tmp_path):
    result = run_scan(fresh(tmp_path), [], FakeProvider([]))

    assert (result.fetched, result.jobs, result.scored) == (0, 0, 0)
    assert result.failed_sources == [] and result.timed_out_sources == []


def test_build_sources_respects_the_enabled_list():
    cfg = SourcesConfig(enabled=["greenhouse"], greenhouse=["acme"])

    assert [s.name for s in build_sources(cfg, None)] == ["greenhouse"]


def test_build_sources_omits_itjobs_without_a_key():
    cfg = SourcesConfig(enabled=["itjobs"])

    assert build_sources(cfg, None) == []


def test_build_sources_includes_itjobs_with_a_key():
    cfg = SourcesConfig(enabled=["itjobs"])

    assert [s.name for s in build_sources(cfg, "key")] == ["itjobs"]


def test_build_sources_skips_an_unknown_source_name():
    cfg = SourcesConfig(enabled=["greenhouse", "nonsense"], greenhouse=["acme"])

    assert [s.name for s in build_sources(cfg, None)] == ["greenhouse"]


def test_scan_result_defaults_are_empty():
    r = ScanResult()

    assert (r.fetched, r.jobs, r.scored) == (0, 0, 0)
    assert r.failed_sources == [] and r.timed_out_sources == []


def test_scan_command_reports_a_helpful_message_when_no_sources_are_enabled(tmp_path):
    """A user whose config enables nothing should be told what to do, not shown
    a scan that silently found zero jobs."""
    from jobhunt.cli import run_scan_command
    from jobhunt.config import Config, LLMConfig, ProfileConfig

    cfg = Config(
        profile=ProfileConfig(name="A", email="a@example.com", location="Porto"),
        llm=LLMConfig(cache_dir=tmp_path / "cache"),
        data_dir=tmp_path,
        sources=SourcesConfig(enabled=[]),
    )

    message = run_scan_command(cfg)

    assert "config.yaml" in message


def test_scan_with_an_empty_profile_stores_jobs_but_skips_scoring(tmp_path):
    """Every job would fail identically, so say it once rather than logging the
    same error hundreds of times — and keep the jobs, which are still real."""
    engine = fresh(tmp_path, with_profile=False)

    result = run_scan(engine, [StubSource("s", [posting()])], FakeProvider([]))

    assert result.jobs == 1
    assert result.scored == 0
    assert result.scoring_skipped is not None
    assert "confirmed evidence" in result.scoring_skipped


def test_scan_command_tells_the_user_how_to_fix_an_empty_profile(tmp_path):
    from jobhunt.cli import run_scan_command
    from jobhunt.config import Config, LLMConfig, ProfileConfig

    cfg = Config(
        profile=ProfileConfig(name="A", email="a@example.com", location="Porto"),
        llm=LLMConfig(cache_dir=tmp_path / "cache"),
        data_dir=tmp_path,
        sources=SourcesConfig(enabled=[]),
    )

    assert "config.yaml" in run_scan_command(cfg)


def test_deadline_stops_between_items_but_cannot_preempt_a_blocking_fetch(tmp_path):
    """Documents a real limit. _drain checks the clock before and after each
    next(), so it stops BETWEEN items — but work inside a single next() cannot be
    interrupted from the consuming loop. That is why HackerNewsSource carries its
    own internal deadline: all of its 400 sequential fetches happen inside one
    next() and no check out here could ever bound them."""
    import time as _time

    class SlowBetweenItems:
        name = "slow-between"

        def fetch(self):
            yield posting(title="A", company="One")
            _time.sleep(0.05)
            yield posting(title="B", company="Two")
            yield posting(title="C", company="Three")

    engine = fresh(tmp_path)
    result = run_scan(
        engine,
        [SlowBetweenItems()],
        FakeProvider([score_payload(), score_payload()]),
        deadline_seconds=0.01,
    )

    assert result.timed_out_sources == ["slow-between"]
    # A and B arrived (the sleep sits inside B's next(), which cannot be
    # interrupted); C was never requested because the deadline stopped the loop.
    assert result.fetched == 2


def test_scoring_stops_at_its_deadline_and_reports_what_is_left(tmp_path):
    """A 200-job scan is 40 minutes; the user must be able to bound it."""
    engine = fresh(tmp_path)
    postings = [posting(title=f"Role {i}", company=f"Co{i}") for i in range(5)]

    result = run_scan(
        engine,
        [StubSource("s", postings)],
        FakeProvider([score_payload() for _ in range(5)]),
        scoring_deadline_seconds=0,
    )

    assert result.jobs == 5
    assert result.scored == 0
    assert result.unscored_remaining == 5


def test_a_previously_unscored_job_is_retried_on_the_next_scan(tmp_path):
    """A job stored while the LLM was down must not stay unscored forever."""
    engine = fresh(tmp_path)

    run_scan(engine, [StubSource("s", [posting()])], FakeProvider([]))  # scoring fails
    second = run_scan(engine, [StubSource("s", [])], FakeProvider([score_payload()]))

    assert second.scored == 1


def test_build_sources_includes_jobspy_sites():
    sources = build_sources(SourcesConfig(enabled=["linkedin", "indeed"]), None)
    assert [s.name for s in sources] == ["linkedin", "indeed"]
