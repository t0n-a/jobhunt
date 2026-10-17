"""The pay-first shortlist: scored, triaged, still-open jobs in the order worth applying to."""

import re
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


# C0, DEL, C1 (ESC starts terminal sequences); bidi marks and overrides can spoof a company name
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def _clean(value: str | None) -> str:
    return _CONTROL.sub("", value or "")


def render_line(r: RankedJob) -> str:
    pay = r.facts.pay_yearly_eur
    if pay is None:
        pay_text = "pay ?"
    else:
        pay_text = f"€{pay:,}" + (" (est.)" if r.facts.pay_estimated else "")
    return (
        f"{pay_text} | fit {r.score.fit_score} | {r.facts.visa} | "
        f"{_clean(r.job.title)} @ {_clean(r.job.company)} | "
        f"{_clean(r.job.location) or 'location ?'} | {_clean(r.job.url)}"
    )
