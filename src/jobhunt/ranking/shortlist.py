"""The pay-first shortlist: scored, triaged, still-open jobs in the order worth applying to."""

from dataclasses import dataclass

from sqlmodel import Session, select

from jobhunt.config import RankingConfig
from jobhunt.jobs.models import Job, JobFacts, Score


@dataclass
class RankedJob:
    job: Job
    score: Score
    facts: JobFacts


def shortlist(session: Session, cfg: RankingConfig, limit: int | None = None) -> list[RankedJob]:
    rows = session.exec(
        select(Job, Score, JobFacts)
        .join(Score, (Score.job_id == Job.id) & (Score.content_hash == Job.content_hash))
        .join(JobFacts, JobFacts.job_id == Job.id)
        .where(JobFacts.content_hash == Job.content_hash)
    ).all()
    ranked = [
        RankedJob(job, score, facts)
        for job, score, facts in rows
        if facts.filtered_reason is None
        and facts.liveness != "expired"
        and score.fit_score >= cfg.shortlist_fit
        and not (facts.level == "mid" and score.fit_score < cfg.strong_fit)
    ]
    ranked.sort(
        key=lambda r: (
            r.facts.visa == "hard",
            r.facts.pay_yearly_eur is None,
            -(r.facts.pay_yearly_eur or 0),
            -r.score.fit_score,
        )
    )
    return ranked[:limit]


def render_line(r: RankedJob) -> str:
    pay = r.facts.pay_yearly_eur
    if pay is None:
        pay_text = "pay ?"
    else:
        pay_text = f"€{pay:,}" + (" (est.)" if r.facts.pay_estimated else "")
    return (
        f"{pay_text} | fit {r.score.fit_score} | {r.facts.visa} | "
        f"{r.job.title} @ {r.job.company} | {r.job.location or 'location ?'} | {r.job.url}"
    )
