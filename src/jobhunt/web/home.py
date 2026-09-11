"""The Home page's numbers and its one next step. No LLM, no side effects."""

from dataclasses import dataclass

from sqlmodel import Session, func, select

from jobhunt.coach.engine import Coach
from jobhunt.jobs.models import Job, Score
from jobhunt.profile.models import EvidenceAtom
from jobhunt.tailor.models import Application


@dataclass(frozen=True)
class NextStep:
    text: str
    href: str | None


@dataclass(frozen=True)
class HomeState:
    confirmed: int
    unconfirmed: int
    jobs_scored: int
    jobs_total: int
    today_title: str | None
    applications: int
    next_step: NextStep


def next_step(
    confirmed: int, unconfirmed: int, jobs_total: int, today_title: str | None
) -> NextStep:
    """The first rule that matches wins. One step, never a checklist."""
    if confirmed + unconfirmed == 0:
        return NextStep("Start by putting something in your profile.", "/import")
    if unconfirmed > 0:
        noun = "atom" if unconfirmed == 1 else "atoms"
        pronoun = "it" if unconfirmed == 1 else "they"
        return NextStep(f"Confirm {unconfirmed} {noun} so {pronoun} can be used.", "/atoms")
    if jobs_total == 0:
        return NextStep("Run `jobhunt scan` to find and score jobs.", None)
    if today_title:
        return NextStep(f"Today: {today_title}", "/today")
    return NextStep("No quest running. Pick a gap to start one.", "/gaps")


def home_state(db: Session) -> HomeState:
    confirmed = db.exec(
        select(func.count()).select_from(EvidenceAtom).where(EvidenceAtom.confirmed)
    ).one()
    unconfirmed = db.exec(
        select(func.count()).select_from(EvidenceAtom).where(EvidenceAtom.confirmed == False)
    ).one()
    jobs_total = db.exec(select(func.count()).select_from(Job)).one()
    jobs_scored = db.exec(select(func.count(func.distinct(Score.job_id)))).one()
    applications = db.exec(select(func.count()).select_from(Application)).one()
    # Coach.current_task reads only the database; the provider is never touched.
    step = Coach(db, llm=None).current_task()  # type: ignore[arg-type]
    today_title = step.title if step is not None else None
    return HomeState(
        confirmed=confirmed,
        unconfirmed=unconfirmed,
        jobs_scored=jobs_scored,
        jobs_total=jobs_total,
        today_title=today_title,
        applications=applications,
        next_step=next_step(confirmed, unconfirmed, jobs_total, today_title),
    )
