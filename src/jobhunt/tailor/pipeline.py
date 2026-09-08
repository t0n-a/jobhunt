from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session

from jobhunt.config import ProfileConfig
from jobhunt.jobs.models import Job
from jobhunt.llm import LLMProvider
from jobhunt.matching.score import EmptyProfileError
from jobhunt.profile.store import AtomStore
from jobhunt.tailor.ats import check_coverage
from jobhunt.tailor.compose import compose_cover_letter, compose_resume
from jobhunt.tailor.keywords import extract_keywords
from jobhunt.tailor.latex import render_pdf
from jobhunt.tailor.models import Application, iter_bullets
from jobhunt.tailor.select import shortlist_atoms
from jobhunt.tailor.verify import verify_document


def tailor_job(
    session: Session,
    job: Job,
    llm: LLMProvider,
    *,
    profile: ProfileConfig,
    limit: int = 25,
) -> Application:
    """Produce one tailored Application for one job.

    The order is the safety argument: keywords, shortlist, compose, VERIFY, and
    only then the cover letter. A letter written alongside the resume can cite a
    claim the verifier goes on to strip, leaving the two documents contradicting
    each other in front of the same reader.
    """
    atoms = AtomStore(session).confirmed_only()
    if not atoms:
        raise EmptyProfileError(
            "Cannot tailor a resume with no confirmed evidence. Confirm at least "
            "one atom first — run the interview or import a CV, then confirm."
        )

    keywords = extract_keywords(job, llm, limit=limit)
    shortlist = shortlist_atoms(atoms, keywords, limit=limit)
    if not shortlist:
        raise EmptyProfileError(
            "None of your confirmed evidence matches this posting. Import or "
            "confirm more atoms, or tailor against a closer job."
        )

    document = compose_resume(job, shortlist, keywords, llm, profile=profile)

    by_id = {atom.id: atom for atom in shortlist}
    verified, dropped = verify_document(document, by_id, set(by_id))

    survivors = [bullet for _, bullet in iter_bullets(verified)]
    cover_letter = compose_cover_letter(job, survivors, llm, profile=profile) if survivors else ""

    application = Application(
        job_id=job.id,
        keywords=keywords,
        resume_json=verified,
        dropped_bullets=dropped,
        cover_letter=cover_letter,
    )
    session.add(application)
    _commit(session)
    session.refresh(application)
    return application


def render_application(
    session: Session,
    application: Application,
    *,
    profile: ProfileConfig,
    data_dir: Path,
) -> Application:
    """Build the PDF and run the ATS self-check over the result."""
    data_dir = Path(data_dir)
    pdf = render_pdf(application, profile, data_dir / "applications")
    report = check_coverage(pdf, application.keywords)

    # Stored relative to data_dir, per the spec's schema: an absolute path
    # breaks the moment the data directory is moved or the repo is cloned
    # elsewhere, and the row outlives both.
    application.pdf_path = str(pdf.relative_to(data_dir))
    application.coverage = report.coverage
    # New list, not a mutation: JSON columns are not mutation-tracked.
    application.missing_keywords = list(report.missing)
    session.add(application)
    _commit(session)
    session.refresh(application)
    return application


def mark_applied(session: Session, application: Application) -> Application:
    application.status = "applied"
    application.applied_at = datetime.now(UTC)
    session.add(application)
    _commit(session)
    session.refresh(application)
    return application


def _commit(session: Session) -> None:
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
