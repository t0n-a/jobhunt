"""Each job's one status and one next action, for the Jobs page. No LLM, no side effects."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, select

from jobhunt.apply.plan import FormPlan
from jobhunt.jobs.models import Job, Score
from jobhunt.tailor.models import Application, iter_bullets

# The two states the user has to act on; the page puts these first and makes them loud.
NEEDS_YOU = ("blocked", "waiting")
_SENT = ("sent", "replied", "interview", "offer")


@dataclass(frozen=True)
class JobState:
    key: str  # unscored | ranked | blocked | waiting | queued | sent | closed
    label: str
    next: str
    href: str  # where the next action happens
    unverified: int  # bullets the user wrote, not drafted from evidence
    application_id: int | None


def job_state(job: Job, score: Score | None, application: Application | None) -> JobState:
    if application is None:
        if score is None:
            return JobState("unscored", "unscored", "Open it to score", f"/jobs/{job.id}", 0, None)
        return JobState("ranked", "ranked", "Tailor a resume", f"/jobs/{job.id}", 0, None)
    app_id, status = application.id, application.status
    href = f"/applications/{app_id}"
    unverified = sum(1 for _, b in iter_bullets(application.resume_json) if b.get("source") == "user")
    if status == "drafted":
        plan = FormPlan.from_json(application.form_plan) if application.form_plan else None
        missing = [f for f in plan.unknown if f.required] if plan else []
        if missing:
            noun = "question" if len(missing) == 1 else "questions"
            return JobState("blocked", "blocked", f"Answer {len(missing)} form {noun}", href, unverified, app_id)
        return JobState("waiting", "needs your yes", "Review and approve", href, unverified, app_id)
    if status in ("queued", "approved"):
        return JobState("queued", "queued", "Nothing to do", href, unverified, app_id)
    if status in _SENT:
        return JobState("sent", status, "View application", href, unverified, app_id)
    # rejected, ghosted, skipped
    return JobState("closed", status, "View application", href, unverified, app_id)


def latest_applications(db: Session) -> dict[int, Application]:
    """Newest application per job."""
    rows = db.exec(select(Application).order_by(Application.created_at)).all()
    return {row.job_id: row for row in rows}


def ago(when: datetime, now: datetime | None = None) -> str:
    """'3h ago' style age, for the job card."""
    now = now or datetime.now(UTC)
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    minutes = max(int((now - when).total_seconds() // 60), 0)
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 60 * 24:
        return f"{minutes // 60}h ago"
    return f"{minutes // (60 * 24)}d ago"


@dataclass(frozen=True)
class Point:
    job_id: int
    x: float
    y: float
    key: str
    flagged: bool


# Fit-map geometry, in SVG user units (viewBox 0 0 520 250).
_DAYS = 14


def fit_y(fit: int) -> float:
    return round(225 - (max(fit, 40) - 40) / 60 * 210, 1)


def chart_points(jobs, scores, states, now: datetime | None = None) -> list[Point]:
    """Scored jobs on the fit map: up is better fit, right is fresher (older than two weeks pins left)."""
    now = now or datetime.now(UTC)
    out = []
    for job in jobs:
        score = scores.get(job.id)
        if score is None:
            continue
        seen = job.first_seen if job.first_seen.tzinfo else job.first_seen.replace(tzinfo=UTC)
        days = min(max((now - seen).total_seconds() / 86400, 0), _DAYS)
        x = round(500 - days / _DAYS * 450, 1)
        state = states[job.id]
        out.append(Point(job.id, x, fit_y(score.fit_score), state.key, state.unverified > 0))
    return out
