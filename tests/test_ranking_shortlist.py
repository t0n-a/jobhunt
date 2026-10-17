import pytest
from sqlmodel import Session

from jobhunt.config import RankingConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job, JobFacts, Score
from jobhunt.ranking.shortlist import RankedJob, render_line, shortlist


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "s.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def add(session, title, fit, pay, visa="easy", level="junior", filtered_reason=None, liveness=None):
    job = Job(title=title, company="Acme", description="d", url="u", content_hash=title)
    session.add(job)
    session.commit()
    session.refresh(job)
    session.add(
        Score(
            job_id=job.id,
            fit_score=fit,
            seniority_fit="ok",
            track="backend",
            rationale="r",
            content_hash=title,
        )
    )
    session.add(
        JobFacts(
            job_id=job.id,
            content_hash=title,
            level=level,
            visa=visa,
            pay_yearly_eur=pay,
            filtered_reason=filtered_reason,
            liveness=liveness,
        )
    )
    session.commit()


def test_orders_by_pay_then_fit_with_hard_visa_last(session):
    add(session, "A", fit=70, pay=50_000, visa="easy")
    add(session, "B", fit=90, pay=40_000, visa="easy")
    add(session, "C", fit=95, pay=None, visa="easy")
    add(session, "D", fit=95, pay=200_000, visa="hard")
    assert [r.job.title for r in shortlist(session, RankingConfig())] == ["A", "B", "C", "D"]


def test_excludes_filtered_expired_low_fit_and_weak_mid(session):
    add(session, "senior", fit=90, pay=1, filtered_reason="senior")
    add(session, "gone", fit=90, pay=1, liveness="expired")
    add(session, "low", fit=30, pay=1)
    add(session, "weak-mid", fit=70, pay=1, level="mid")
    add(session, "strong-mid", fit=80, pay=1, level="mid")
    assert [r.job.title for r in shortlist(session, RankingConfig())] == ["strong-mid"]


def test_render_line_marks_estimates():
    job = Job(
        title="Backend Engineer",
        company="Acme",
        location="Berlin, Germany",
        description="d",
        url="u",
        content_hash="h",
    )
    score = Score(
        job_id=1, fit_score=80, seniority_fit="ok", track="b", rationale="r", content_hash="h"
    )
    facts = JobFacts(
        job_id=1,
        content_hash="h",
        level="junior",
        visa="easy",
        pay_yearly_eur=65_000,
        pay_estimated=True,
    )
    line = "€65,000 (est.) | fit 80 | easy | Backend Engineer @ Acme | Berlin, Germany | u"
    assert render_line(RankedJob(job, score, facts)) == line
    facts.pay_yearly_eur = None
    assert render_line(RankedJob(job, score, facts)).startswith("pay ? | fit 80")


def test_render_line_strips_control_characters():
    job = Job(title="\x1b[31mEvil\x1b[0m", company="A\x07cme", location="B\x9berlin",
              description="d", url="u\x1b]0;x", content_hash="h")
    score = Score(job_id=1, fit_score=80, seniority_fit="ok", track="b", rationale="r",
                  content_hash="h")
    facts = JobFacts(job_id=1, content_hash="h", level="junior", visa="easy")
    line = render_line(RankedJob(job, score, facts))
    assert "\x1b" not in line and "\x07" not in line and "\x9b" not in line
    assert "[31mEvil[0m @ Acme | Berlin | u]0;x" in line
