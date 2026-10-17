import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlmodel import Session, select

from jobhunt.config import RankingConfig, SourcesConfig
from jobhunt.counselor.scoring import scoring_block
from jobhunt.jobs.liveness import check_liveness
from jobhunt.jobs.models import Job, JobFacts, Score
from jobhunt.jobs.store import JobStore
from jobhunt.llm import LLMError, LLMProvider
from jobhunt.matching.score import score_job
from jobhunt.profile.store import AtomStore
from jobhunt.ranking.rules import triage
from jobhunt.ranking.shortlist import shortlist
from jobhunt.sources.ats import AshbySource, GreenhouseSource, LeverSource
from jobhunt.sources.hackernews import HackerNewsSource
from jobhunt.sources.jobspy_source import JobSpySource
from jobhunt.sources.portugal import ITJobsSource, LandingJobsSource
from jobhunt.sources.remote_boards import (
    RemoteOkSource,
    RemotiveSource,
    WeWorkRemotelySource,
)

logger = logging.getLogger(__name__)

# One source must not be able to consume an entire scan. Hacker News in
# particular issues up to 400 sequential comment fetches, each with its own
# 30-second budget, so without a ceiling a degraded network turns one adapter
# into a scan that never visibly finishes.
SOURCE_DEADLINE_SECONDS = 180

# Scoring is one LLM call per job, so a first scan of 200 postings is roughly
# forty minutes of silence. A scan the user avoids running is a coach that goes
# stale, so scoring is bounded too and says what it left behind.
SCORING_DEADLINE_SECONDS = 1800

# How many previously-unscored jobs one scan will retry. A job stored while the
# LLM was down must not stay unscored forever, but neither should a backlog turn
# every scan into a full rescore.
SWEEP_LIMIT = 100


@dataclass
class ScanResult:
    fetched: int = 0
    jobs: int = 0
    scored: int = 0
    failed_sources: list[str] = field(default_factory=list)
    timed_out_sources: list[str] = field(default_factory=list)
    scoring_skipped: str | None = None
    unscored_remaining: int = 0
    filtered: int = 0
    expired: int = 0


def build_sources(cfg: SourcesConfig, itjobs_key: str | None) -> list:
    """Instantiate the enabled sources. Sources lacking config are omitted."""
    candidates = {
        "greenhouse": lambda: GreenhouseSource(cfg.greenhouse),
        "lever": lambda: LeverSource(cfg.lever),
        "ashby": lambda: AshbySource(cfg.ashby),
        "remotive": RemotiveSource,
        "remoteok": RemoteOkSource,
        "weworkremotely": WeWorkRemotelySource,
        "landingjobs": LandingJobsSource,
        "hackernews": lambda: HackerNewsSource(
            cfg.location_keywords, cfg.role_keywords, SOURCE_DEADLINE_SECONDS
        ),
        "itjobs": lambda: ITJobsSource(itjobs_key),
        "linkedin": lambda: JobSpySource("linkedin", cfg.jobspy),
        "indeed": lambda: JobSpySource("indeed", cfg.jobspy),
    }
    sources = []
    for name in cfg.enabled:
        factory = candidates.get(name)
        if factory is None:
            logger.warning("unknown source in config: %s", name)
            continue
        if name == "itjobs" and not itjobs_key:
            logger.info("itjobs enabled but no API key present; skipping")
            continue
        sources.append(factory())
    return sources


def _drain(source, deadline_seconds: int) -> tuple[list, bool]:
    """Pull postings from one source, stopping at its deadline.

    Adapters are generators, so this consumes them incrementally and can stop
    partway. Returns what was collected and whether the deadline was hit.
    """
    collected = []
    started = time.monotonic()
    stream = iter(source.fetch())
    while True:
        # next() rather than a for-loop: the clock must advance while the adapter
        # is WORKING, not only when it yields. Hacker News walks up to 400
        # comments and, once role-filtered, yields nothing for most of them — a
        # yield-only check would never fire.
        if time.monotonic() - started > deadline_seconds:
            logger.warning(
                "source %s hit the %ss deadline after %d postings; keeping those",
                source.name,
                deadline_seconds,
                len(collected),
            )
            return collected, True
        try:
            posting = next(stream)
        except StopIteration:
            return collected, False
        collected.append(posting)
        if time.monotonic() - started > deadline_seconds:
            logger.warning(
                "source %s hit the %ss deadline after %d postings; keeping those",
                source.name,
                deadline_seconds,
                len(collected),
            )
            return collected, True
    return collected, False


def run_scan(
    engine,
    sources: list,
    llm: LLMProvider,
    deadline_seconds: int = SOURCE_DEADLINE_SECONDS,
    scoring_deadline_seconds: int = SCORING_DEADLINE_SECONDS,
    ranking: RankingConfig | None = None,
    liveness_client: httpx.Client | None = None,
) -> ScanResult:
    """Fetch every source, dedupe into jobs, and score anything unscored.

    Three isolation properties, all required by spec §9:
    a failing source is recorded and skipped rather than aborting the scan;
    a source that overruns its deadline keeps what it collected;
    a failing score leaves the job stored but unscored, never dropped.
    """
    ranking = ranking or RankingConfig()
    result = ScanResult()
    postings = []

    for source in sources:
        try:
            fetched, timed_out = _drain(source, deadline_seconds)
        except Exception as exc:  # noqa: BLE001 - isolation is the requirement
            logger.warning("source %s failed: %s", source.name, exc)
            result.failed_sources.append(source.name)
            continue
        if timed_out:
            result.timed_out_sources.append(source.name)
        postings.extend(fetched)

    result.fetched = len(postings)

    with Session(engine) as session:
        job_store = JobStore(session)
        # Confirmed atoms only: scoring against unreviewed model output would
        # rank the candidate on claims they never checked.
        atoms = AtomStore(session).confirmed_only()
        notes = scoring_block(session)

        if not atoms:
            # Every job would fail identically; say it once, clearly, instead of
            # logging the same error hundreds of times.
            result.scoring_skipped = "no confirmed evidence atoms — jobs were stored but not scored"
            logger.warning("scan: %s", result.scoring_skipped)

        seen_job_ids: set[int] = set()
        for posting in postings:
            try:
                job = job_store.upsert_posting(posting)
                seen_job_ids.add(job.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not store posting %s: %s", posting.url, exc)

        result.jobs = len(seen_job_ids)

        _triage_all(session, ranking)
        filtered_ids = {
            f.job_id
            for f in session.exec(select(JobFacts).where(JobFacts.filtered_reason.is_not(None)))
        }
        result.filtered = len(filtered_ids & seen_job_ids)

        queue = [
            j
            for j in list(seen_job_ids) + _sweep_unscored(session, skip=seen_job_ids)
            if j not in filtered_ids
        ]
        started = time.monotonic()
        for index, job_id in enumerate(queue if atoms else []):
            if time.monotonic() - started >= scoring_deadline_seconds:
                logger.warning(
                    "scoring hit the %ss deadline after %d jobs; %d left for the next scan",
                    scoring_deadline_seconds,
                    result.scored,
                    len(queue) - index,
                )
                break
            if index and index % 10 == 0:
                logger.info("scoring %d/%d", index, len(queue))
            job = job_store.get(job_id)
            already = session.exec(
                select(Score).where(Score.job_id == job_id, Score.content_hash == job.content_hash)
            ).first()
            if already is not None:
                continue
            try:
                score = score_job(job, atoms, llm, notes=notes)
            except LLMError as exc:
                logger.warning("scoring failed for job %s: %s", job_id, exc)
                continue
            session.add(score)
            try:
                session.commit()
                result.scored += 1
            except Exception:
                session.rollback()
                raise

        result.unscored_remaining = len(
            [j for j in _unscored_job_ids(session) if j not in filtered_ids]
        )
        if liveness_client is not None:
            result.expired = _check_liveness(session, ranking, liveness_client)

    return result


def _triage_all(session: Session, cfg: RankingConfig) -> None:
    """Compute rule facts for jobs lacking them or whose text changed since."""
    facts = {f.job_id: f for f in session.exec(select(JobFacts)).all()}
    for job in session.exec(select(Job)).all():
        old = facts.get(job.id)
        if old is not None and old.content_hash == job.content_hash:
            continue
        try:
            new = triage(job, cfg)
        except Exception:  # noqa: BLE001 - one bad job must not abort the scan
            logger.warning("triage failed for job %s; skipping", job.id, exc_info=True)
            continue
        if old is not None:
            new.liveness, new.liveness_checked_at = old.liveness, old.liveness_checked_at
            session.delete(old)
            session.flush()
        session.add(new)
    session.commit()


def _check_liveness(session: Session, cfg: RankingConfig, client: httpx.Client) -> int:
    """Probe the top shortlist entries not checked in 24h; return how many are expired."""
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    for r in shortlist(session, cfg)[: cfg.liveness_check_limit]:
        checked = r.facts.liveness_checked_at
        if checked is not None and checked.replace(tzinfo=UTC) > cutoff:
            continue
        r.facts.liveness = check_liveness(r.job.url, client)
        r.facts.liveness_checked_at = datetime.now(UTC)
        session.add(r.facts)
    session.commit()
    return len(session.exec(select(JobFacts).where(JobFacts.liveness == "expired")).all())


def _unscored_job_ids(session: Session) -> list[int]:
    """Jobs whose CURRENT text has no score.

    A job scored before its description grew counts as unscored: the old
    judgement describes text the job no longer has.
    """
    scored = {(score.job_id, score.content_hash) for score in session.exec(select(Score)).all()}
    return [
        job.id
        for job in session.exec(select(Job)).all()
        if (job.id, job.content_hash) not in scored
    ]


def _sweep_unscored(session: Session, *, skip: set[int]) -> list[int]:
    """A bounded retry of jobs an earlier scan left unscored.

    run_scan only ever looked at jobs seen in THIS run, so a job stored while the
    LLM was down would stay unscored forever.
    """
    return [job_id for job_id in _unscored_job_ids(session) if job_id not in skip][:SWEEP_LIMIT]
