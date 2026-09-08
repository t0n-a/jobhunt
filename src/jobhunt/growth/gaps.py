from collections import defaultdict
from dataclasses import dataclass, field

from sqlmodel import Session, select

from jobhunt.jobs.models import Job, Score
from jobhunt.matching.skills import canonical_skill


@dataclass
class Gap:
    """One missing skill, and how much it is costing the user."""

    skill: str
    job_count: int
    weight: float
    example_titles: list[str] = field(default_factory=list)


def compute_gaps(session: Session, *, min_fit: int = 40, limit: int = 20) -> list[Gap]:
    """Rank missing skills by how much good work they block.

    Weight sums fit/100 across the jobs wanting each skill, so one strong match
    outranks several weak ones. job_count is kept alongside because it is the
    number shown to the user — "appears in 14 of your 20 best-fit jobs" — and a
    weight is not a sentence.
    """
    jobs = {job.id: job for job in session.exec(select(Job)).all()}
    scores = session.exec(select(Score).order_by(Score.scored_at)).all()

    # Newest score per job, and only if it still describes the job's current
    # text — a stale score's gaps came from a description the job no longer has.
    current: dict[int, Score] = {}
    for score in scores:
        job = jobs.get(score.job_id)
        if job is not None and job.content_hash == score.content_hash:
            current[score.job_id] = score

    counts: dict[str, int] = defaultdict(int)
    weights: dict[str, float] = defaultdict(float)
    titles: dict[str, list[str]] = defaultdict(list)

    for job_id, score in current.items():
        if score.fit_score < min_fit:
            continue
        job = jobs[job_id]
        seen_in_this_job: set[str] = set()
        for raw in score.missing_skills:
            skill = canonical_skill(raw)
            if not skill or skill in seen_in_this_job:
                continue
            seen_in_this_job.add(skill)
            counts[skill] += 1
            weights[skill] += score.fit_score / 100
            if len(titles[skill]) < 5:
                titles[skill].append(job.title)

    gaps = [
        Gap(
            skill=skill,
            job_count=counts[skill],
            weight=weights[skill],
            example_titles=titles[skill],
        )
        for skill in counts
    ]
    gaps.sort(key=lambda g: (g.weight, g.job_count), reverse=True)
    return gaps[:limit]
