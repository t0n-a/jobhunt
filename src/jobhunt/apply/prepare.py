"""Read an application form ahead of time, plan its answers, and open it for the candidate."""

from __future__ import annotations

import logging

import httpx
from sqlmodel import Session

from jobhunt.answers import load_answers
from jobhunt.apply.ats import detect_ats
from jobhunt.apply.browser import run_form, scan_fields
from jobhunt.apply.form import FormField, greenhouse_form
from jobhunt.apply.plan import FormPlan, plan_form, save_plan
from jobhunt.apply.tracker import set_status
from jobhunt.config import Config
from jobhunt.jobs.models import Job, JobFacts
from jobhunt.profile.store import AtomStore
from jobhunt.ranking.geo import EU_EEA
from jobhunt.tailor.models import Application

log = logging.getLogger(__name__)

# Why: assisted apply is for the candidate to start from a queued or approved application only.
_APPLYABLE = ("queued", "approved")


def _scan(url: str) -> list[FormField] | None:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url)
                return scan_fields(page)
            finally:
                browser.close()
    except Exception:  # an unreadable form just means no plan
        log.warning("could not scan %s", url, exc_info=True)
        return None


def fields_for(target, http: httpx.Client, *, scan=None) -> list[FormField] | None:
    if target.kind == "greenhouse":
        return greenhouse_form(target, http)
    return (scan or _scan)(target.apply_url)


def prepare_plan(
    session: Session, application: Application, config: Config, llm, http, *, scan=None
) -> FormPlan | None:
    job = session.get(Job, application.job_id)
    target = detect_ats(job.url) if job else None
    if target is None:
        return None
    fields = fields_for(target, http, scan=scan)
    if fields is None:
        return None
    facts = session.get(JobFacts, job.id)
    try:
        answers = load_answers(config.answers_path)
    except ValueError as exc:
        log.warning("answers file unreadable: %s", exc)
        answers = None
    plan = plan_form(
        fields,
        profile=config.profile,
        answers=answers,
        cover_letter=application.cover_letter,
        pdf_path=config.data_dir / application.pdf_path if application.pdf_path else None,
        eu_job=facts is None or facts.country is None or facts.country in EU_EEA,
        atoms=AtomStore(session).confirmed_only(),
        llm=llm,
    )
    save_plan(session, application, plan)
    return plan


def assisted_apply(
    session: Session,
    application: Application,
    config: Config,
    llm,
    http,
    *,
    runner=run_form,
    ask=input,
    scan=None,
) -> str:
    n = application.id
    if application.status not in _APPLYABLE:
        return f"#{n} is {application.status}; nothing to apply."
    job = session.get(Job, application.job_id)
    target = detect_ats(job.url)
    # The stored plan is what the candidate reviewed; only plan afresh when there is none.
    plan = FormPlan.from_json(application.form_plan) if target and application.form_plan else None
    if plan is None and target:
        plan = prepare_plan(session, application, config, llm, http, scan=scan)
    if target is None or plan is None:
        return f"No supported form for #{n}. Apply here: {job.url}"
    result = runner(
        target.apply_url,
        plan,
        headless=False,
        submit=False,
        allow_obstacles=True,
        wait_for_human=True,
        screenshot_dir=config.data_dir / "screenshots",
    )
    if result.status != "ready":
        return f"Couldn't prepare the form: {result.reason}. Apply here: {job.url}"
    if ask("Did you submit it? [y/N] ").strip().lower() == "y":
        application.channel = "assisted"
        set_status(session, application, "sent", note="assisted")
        return f"Marked #{n} as sent."
    return f'Not marked as sent. Send "sent {n}" once it\'s out.'
