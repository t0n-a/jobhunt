import os
import shutil
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from jobhunt.coach.engine import Coach
from jobhunt.coach.models import Energy
from jobhunt.config import Config
from jobhunt.counselor.engine import get_or_create_conversation, recent_turns, respond
from jobhunt.counselor.models import NOTE_KINDS, CounselorNote, Turn
from jobhunt.counselor.proposals import accept as accept_proposal
from jobhunt.counselor.proposals import reject as reject_proposal
from jobhunt.counselor.scoring import open_notes, scoring_block
from jobhunt.db import create_engine_for, init_db
from jobhunt.growth.gaps import compute_gaps
from jobhunt.jobs.models import Job, Score
from jobhunt.jobs.store import JobStore
from jobhunt.linkedin.build import build_profile
from jobhunt.linkedin.diff import diff_field
from jobhunt.linkedin.fields import field_spec, over_limit_message
from jobhunt.linkedin.store import LinkedinStore
from jobhunt.llm import LLMError, LLMProvider
from jobhunt.matching.score import EmptyProfileError, score_job
from jobhunt.profile.importers.coursework import import_coursework
from jobhunt.profile.importers.cv import import_cv
from jobhunt.profile.importers.github import import_github
from jobhunt.profile.models import EvidenceAtom
from jobhunt.profile.store import AtomStore
from jobhunt.tailor.models import Application, delete_bullet, edit_bullet, iter_bullets
from jobhunt.tailor.pipeline import mark_applied, render_application, tailor_job
from jobhunt.web.deps import get_llm, get_store
from jobhunt.web.flash import FLASH_COOKIE, flash, read_flash
from jobhunt.web.home import home_state

HERE = Path(__file__).parent


@contextmanager
def _importer_errors():
    """Translate importer exceptions into HTTP responses.

    ValueError  -> 400  bad user input (unsupported file, corrupt docx, no extractable text)
    LLMError    -> 502  the model returned something structurally unusable; retryable
    RuntimeError-> 503  environment fault the user can act on (missing pdftotext, rate limit)
    """
    try:
        yield
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="jobhunt")

    engine = create_engine_for(config.data_dir / "jobhunt.db")
    init_db(engine)
    app.state.engine = engine
    app.state.config = config

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals["profile_name"] = config.profile.name
    templates.env.globals["read_flash"] = read_flash
    app.state.templates = templates

    @app.middleware("http")
    async def clear_flash(request: Request, call_next):
        """A flash is shown once: the full-page GET that follows the redirect."""
        response = await call_next(request)
        consumed = (
            request.method == "GET"
            and FLASH_COOKIE in request.cookies
            and "HX-Request" not in request.headers
            and response.headers.get("content-type", "").startswith("text/html")
        )
        if consumed:
            response.delete_cookie(FLASH_COOKIE, path="/")
        return response

    @app.exception_handler(StarletteHTTPException)
    async def html_errors(request: Request, exc: StarletteHTTPException):
        """Browsers get the error inside the layout; HTMX and API clients keep JSON.

        The default handler's JSON is what every existing test asserts on, so
        the branch is on the request, never on the status code.
        """
        wants_html = "text/html" in request.headers.get("accept", "")
        if not wants_html or "HX-Request" in request.headers:
            return await http_exception_handler(request, exc)
        titles = {
            400: "That input could not be used",
            404: "Not found",
            409: "That is out of date",
            502: "The model gave up",
            503: "Something is missing",
        }
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "status_code": exc.status_code,
                "title": titles.get(exc.status_code, "Something went wrong"),
                "detail": exc.detail,
                "back": request.headers.get("referer") or "/",
            },
            status_code=exc.status_code,
        )

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/")
    def home_page(request: Request):
        with Session(app.state.engine) as db:
            return templates.TemplateResponse(request, "home.html", {"state": home_state(db)})

    @app.get("/atoms")
    def atoms_page(
        request: Request,
        store: AtomStore = Depends(get_store),
    ):
        return templates.TemplateResponse(request, "atoms.html", {"atoms": store.list()})

    @app.get("/import")
    def import_page(request: Request):
        return templates.TemplateResponse(request, "import.html", {})

    def _imported(count: int):
        response = RedirectResponse("/atoms", status_code=303)
        if count == 0:
            return flash(response, "Nothing found to import.", "warn")
        return flash(response, f"{count} atoms imported — confirm the ones that are right.")

    @app.post("/import/cv")
    def import_cv_route(
        file: UploadFile = File(...),
        store: AtomStore = Depends(get_store),
        llm: LLMProvider = Depends(get_llm),
    ):
        raw_filename = file.filename or ""
        suffix = Path(raw_filename).suffix.lower()
        if suffix not in (".pdf", ".docx", ".txt"):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported CV format: {suffix or '(no extension)'}",
            )

        sanitized_name = Path(raw_filename).name
        filename = sanitized_name if sanitized_name else f"upload{suffix}"

        uploads = config.data_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        target = uploads / filename
        with target.open("wb") as out:
            shutil.copyfileobj(file.file, out)

        with _importer_errors():
            atoms = import_cv(target, llm)

        store.add_many(atoms)
        return _imported(len(atoms))

    @app.post("/import/github")
    def import_github_route(
        username: str = Form(...),
        store: AtomStore = Depends(get_store),
        llm: LLMProvider = Depends(get_llm),
    ):
        try:
            with _importer_errors():
                atoms = import_github(username, llm, token=os.environ.get("GITHUB_TOKEN"))
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        store.add_many(atoms)
        return _imported(len(atoms))

    @app.post("/import/coursework")
    def import_coursework_route(
        text: str = Form(...),
        institution: str = Form(...),
        store: AtomStore = Depends(get_store),
        llm: LLMProvider = Depends(get_llm),
    ):
        with _importer_errors():
            atoms = import_coursework(text, institution, llm)

        store.add_many(atoms)
        return _imported(len(atoms))

    @app.post("/atoms/{atom_id}/confirm")
    def atom_confirm(request: Request, atom_id: int, store: AtomStore = Depends(get_store)):
        try:
            atom = store.update(atom_id, confirmed=True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(request, "partials/atom_row.html", {"atom": atom})

    @app.post("/atoms/{atom_id}/edit")
    def atom_edit(
        request: Request,
        atom_id: int,
        title: str = Form(...),
        description: str = Form(...),
        skills: str = Form(""),
        store: AtomStore = Depends(get_store),
    ):
        parsed = [s.strip() for s in skills.split(",") if s.strip()]
        try:
            atom = store.update(atom_id, title=title, description=description, skills=parsed)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(request, "partials/atom_row.html", {"atom": atom})

    @app.post("/atoms/{atom_id}/delete")
    def atom_delete(atom_id: int, store: AtomStore = Depends(get_store)):
        store.delete(atom_id)
        return HTMLResponse("")

    def _latest_scores(db: Session) -> dict[int, Score]:
        """Newest score per job, but only if it still describes the CURRENT text.

        upsert_posting recomputes content_hash when a longer description arrives,
        and a rescore can then fail. Without this filter the inbox keeps showing
        the old fit for a description the job no longer has.
        """
        rows = db.exec(select(Score).order_by(Score.scored_at)).all()
        current = {job.id: job.content_hash for job in db.exec(select(Job)).all()}
        return {row.job_id: row for row in rows if current.get(row.job_id) == row.content_hash}

    @app.get("/jobs")
    def jobs_inbox(request: Request):
        with Session(app.state.engine) as db:
            jobs = JobStore(db).list_jobs()
            scores = _latest_scores(db)
            ranked = sorted(
                jobs,
                key=lambda j: scores[j.id].fit_score if j.id in scores else -1,
                reverse=True,
            )
            tracks = Counter(scores[j.id].track for j in ranked if j.id in scores).most_common()
            return templates.TemplateResponse(
                request,
                "inbox.html",
                {"jobs": ranked, "scores": scores, "tracks": tracks},
            )

    @app.get("/jobs/{job_id}")
    def job_detail(request: Request, job_id: int):
        with Session(app.state.engine) as db:
            job = JobStore(db).get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="No such job")
            score = _latest_scores(db).get(job_id)
            return templates.TemplateResponse(
                request, "job_detail.html", {"job": job, "score": score}
            )

    @app.post("/jobs/paste")
    def job_paste(
        title: str = Form(...),
        company: str = Form(...),
        url: str = Form(""),
        description: str = Form(...),
        llm: LLMProvider = Depends(get_llm),
    ):
        with Session(app.state.engine) as db:
            job = JobStore(db).add_manual(title, company, description, url)
            atoms = AtomStore(db).confirmed_only()
            with _importer_errors():
                score = score_job(job, atoms, llm, notes=scoring_block(db))
            db.add(score)
            db.commit()
            title, company = job.title, job.company
        return flash(RedirectResponse("/jobs", status_code=303), f"Scored: {title} at {company}.")

    @app.post("/jobs/{job_id}/rescore")
    def job_rescore(job_id: int, llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            job = JobStore(db).get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="No such job")
            atoms = AtomStore(db).confirmed_only()
            with _importer_errors():
                score = score_job(job, atoms, llm, notes=scoring_block(db))
            for old in db.exec(select(Score).where(Score.job_id == job_id)).all():
                db.delete(old)
            db.add(score)
            db.commit()
        return flash(RedirectResponse(f"/jobs/{job_id}", status_code=303), "Re-scored.")

    def _coach(db: Session, llm) -> Coach:
        return Coach(
            db,
            llm,
            github_user=config.profile.github_username,
            github_token=os.environ.get("GITHUB_TOKEN"),
        )

    def _energy(raw: str) -> Energy:
        """An unknown level must not 500 the one page the user opens daily."""
        try:
            return Energy(raw)
        except ValueError:
            return Energy.NORMAL

    @app.get("/today")
    def today_page(request: Request, llm: LLMProvider = Depends(get_llm)):
        energy = _energy(request.query_params.get("energy", "normal"))
        with Session(app.state.engine) as db:
            today = _coach(db, llm).today(energy)
            return templates.TemplateResponse(
                request, "today.html", {"today": today, "energy": energy}
            )

    @app.post("/today/energy")
    def today_energy(energy: str = Form("normal")):
        return RedirectResponse(f"/today?energy={_energy(energy).value}", status_code=303)

    @app.post("/today/done")
    def today_done(
        proof_note: str = Form(""),
        llm: LLMProvider = Depends(get_llm),
    ):
        with Session(app.state.engine) as db:
            atom = _coach(db, llm).complete_current(proof_note or None)
        response = RedirectResponse("/today", status_code=303)
        if atom is not None:
            return flash(response, "Done. That earns an evidence line — confirm it on Evidence.")
        return flash(response, "Nothing to mark done.", "warn")

    @app.post("/today/swap")
    def today_swap(llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            _coach(db, llm).swap_current()
        return flash(RedirectResponse("/today", status_code=303), "Swapped.")

    @app.post("/today/verify")
    def today_verify(request: Request, llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            result = _coach(db, llm).verify_current()
        return templates.TemplateResponse(
            request, "partials/verify_result.html", {"result": result}
        )

    @app.get("/gaps")
    def gaps_page(request: Request):
        with Session(app.state.engine) as db:
            return templates.TemplateResponse(request, "gaps.html", {"gaps": compute_gaps(db)})

    @app.post("/gaps/{skill}/start")
    def gap_start(skill: str, llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            gap = next((g for g in compute_gaps(db) if g.skill == skill), None)
            if gap is None:
                raise HTTPException(status_code=404, detail="No such gap")
            with _importer_errors():
                _coach(db, llm).start_quest(gap)
        return flash(
            RedirectResponse("/today", status_code=303),
            f"Quest started for {skill}. Your first task is on Today.",
        )

    def _application(db: Session, application_id: int) -> Application:
        application = db.get(Application, application_id)
        if application is None:
            raise HTTPException(status_code=404, detail="No such application")
        return application

    @app.post("/jobs/{job_id}/tailor")
    def job_tailor(job_id: int, llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            job = JobStore(db).get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="No such job")
            with _importer_errors():
                application = tailor_job(db, job, llm, profile=config.profile)
            application_id = application.id
        return flash(
            RedirectResponse(f"/applications/{application_id}", status_code=303),
            "Resume tailored — read the header before you send it.",
        )

    @app.get("/applications")
    def applications_page(request: Request):
        with Session(app.state.engine) as db:
            applications = db.exec(
                select(Application).order_by(Application.created_at.desc())
            ).all()
            jobs = {job.id: job for job in db.exec(select(Job)).all()}
            return templates.TemplateResponse(
                request,
                "applications.html",
                {"applications": list(applications), "jobs": jobs},
            )

    @app.get("/applications/{application_id}")
    def application_detail(request: Request, application_id: int):
        with Session(app.state.engine) as db:
            application = _application(db, application_id)
            job = JobStore(db).get(application.job_id)
            return templates.TemplateResponse(
                request,
                "application_detail.html",
                {
                    "application": application,
                    "job": job,
                    "bullets": iter_bullets(application.resume_json),
                },
            )

    @app.post("/applications/{application_id}/bullets/{index}")
    def application_edit_bullet(
        request: Request, application_id: int, index: int, text: str = Form(...)
    ):
        with Session(app.state.engine) as db:
            application = _application(db, application_id)
            try:
                # Assign a new dict: JSON columns are not mutation-tracked, and
                # an in-place edit is silently discarded on commit.
                updated = edit_bullet(application.resume_json, index, text)
            except IndexError as exc:
                raise HTTPException(status_code=404, detail="No such bullet") from exc
            application.resume_json = updated
            db.add(application)
            db.commit()
            # Render from the document we just wrote, not from a re-read row. A
            # delete committed between this commit and a refresh would leave the
            # index missing and raise KeyError — a 500 for a request that
            # succeeded.
            bullet = dict(iter_bullets(updated))[index]
            return templates.TemplateResponse(
                request,
                "partials/bullet_row.html",
                {"application": application, "index": index, "bullet": bullet},
            )

    @app.post("/applications/{application_id}/bullets/{index}/delete")
    def application_delete_bullet(request: Request, application_id: int, index: int):
        with Session(app.state.engine) as db:
            application = _application(db, application_id)
            try:
                updated = delete_bullet(application.resume_json, index)
            except IndexError as exc:
                raise HTTPException(status_code=404, detail="No such bullet") from exc
            application.resume_json = updated
            db.add(application)
            db.commit()
            # Re-render the WHOLE list, not the removed row. Deleting shifts every
            # later ordinal down, and a page still showing the old numbers sends
            # the next edit to a different bullet, overwriting a line the user
            # never touched, with nothing on screen to show it happened.
            return templates.TemplateResponse(
                request,
                "partials/bullet_list.html",
                {"application": application, "bullets": iter_bullets(updated)},
            )

    @app.post("/applications/{application_id}/render")
    def application_render(application_id: int):
        with Session(app.state.engine) as db:
            application = _application(db, application_id)
            with _importer_errors():
                rendered = render_application(
                    db, application, profile=config.profile, data_dir=config.data_dir
                )
            coverage = rendered.coverage
        response = RedirectResponse(f"/applications/{application_id}", status_code=303)
        if coverage is None:
            return flash(response, "PDF rendered.")
        elif coverage < 60:
            return flash(response, f"PDF rendered — {coverage:.0f}% keyword coverage.", "warn")
        else:
            return flash(response, f"PDF rendered — {coverage:.0f}% keyword coverage.", "ok")

    @app.get("/applications/{application_id}/pdf")
    def application_pdf(application_id: int):
        with Session(app.state.engine) as db:
            application = _application(db, application_id)
            # pdf_path is stored relative to data_dir; resolve it here.
            path = config.data_dir / application.pdf_path if application.pdf_path else None
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="Not rendered yet")
        return FileResponse(path, media_type="application/pdf", filename=path.name)

    @app.post("/applications/{application_id}/applied")
    def application_applied(application_id: int):
        with Session(app.state.engine) as db:
            mark_applied(db, _application(db, application_id))
        return flash(RedirectResponse("/applications", status_code=303), "Marked as applied.")

    def _previous_texts(store: LinkedinStore, version) -> dict[tuple[str, int], str]:
        """This version's predecessor's text, keyed the same way a field is: by
        (name, position). Empty when there is no predecessor."""
        previous = store.previous(version)
        if previous is None:
            return {}
        return {(field.name, field.position): field.text for field in store.fields_for(previous)}

    def _field_context(store: LinkedinStore, field, diff, *, readonly: bool) -> dict:
        """One field's render context: its limit, its diff and its approval state.

        The single place this dict is built, so `_linkedin_view` and both HTMX
        partial routes cannot quietly disagree about what a field looks like.
        `over_limit_message` is called unconditionally — it answers "" for text
        within every limit, so no caller needs to guard the call.
        """
        spec = field_spec(field.name)
        diff_has_changes = any(kind != "same" for kind, _ in diff)
        return {
            "field": field,
            "spec": spec,
            "diff": diff,
            "diff_has_changes": diff_has_changes,
            "publishable": store.is_publishable(field),
            "limit_message": over_limit_message(spec, field.text),
            "readonly": readonly,
        }

    def _linkedin_view(db, version, *, readonly: bool = False):
        """The page's data: each field with its limit, its diff and its state."""
        store = LinkedinStore(db)
        if version is None:
            return {"version": None, "fields": [], "dropped": [], "readonly": readonly}
        previous_texts = _previous_texts(store, version)
        fields = [
            _field_context(
                store,
                field,
                diff_field(previous_texts.get((field.name, field.position), ""), field.text),
                readonly=readonly,
            )
            for field in store.fields_for(version)
        ]
        return {
            "version": version,
            "fields": fields,
            "dropped": version.dropped,
            "readonly": readonly,
        }

    def _ensure_latest(store: LinkedinStore, field) -> None:
        """Refuse to mutate a field that does not belong to the latest version.

        The UI hides the edit form and Approve button on an older version (Fix
        1a), but that is cosmetic: a hand-crafted POST must not be able to
        rewrite history just because nothing in the markup stopped it.
        """
        latest = store.latest()
        if latest is None or field.profile_id != latest.id:
            raise HTTPException(
                status_code=409,
                detail="This field belongs to an older version and cannot be changed.",
            )

    @app.get("/linkedin")
    def linkedin_page(request: Request):
        with Session(app.state.engine) as db:
            context = _linkedin_view(db, LinkedinStore(db).latest())
            return templates.TemplateResponse(request, "linkedin.html", context)

    @app.post("/linkedin/generate")
    def linkedin_generate(request: Request, llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            try:
                version = build_profile(db, llm, profile=config.profile)
                dropped = len(version.dropped or [])
            except EmptyProfileError as exc:
                # Not a 502: this is the user's own store being empty, and the
                # page can say what to do about it. Caught BEFORE _importer_errors,
                # which would map it to a provider fault.
                context = _linkedin_view(db, LinkedinStore(db).latest())
                context["refusal"] = str(exc)
                return templates.TemplateResponse(request, "linkedin.html", context)
            except LLMError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        response = RedirectResponse("/linkedin", status_code=303)
        if dropped > 0:
            return flash(
                response, f"Profile built. {dropped} fields dropped as unverified.", "warn"
            )
        return flash(response, "Profile built.")

    @app.post("/linkedin/fields/{field_id}")
    def linkedin_edit_field(request: Request, field_id: int, text: str = Form(...)):
        with Session(app.state.engine) as db:
            store = LinkedinStore(db)
            existing = store.get_field(field_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"No LinkedIn field with id {field_id}")
            _ensure_latest(store, existing)
            try:
                field = store.edit_field(field_id, text)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            # The user rewrote the text: a diff against the model's draft is no
            # longer the comparison they care about, so this collapses to one
            # "same" segment and the template hides the change notice. That is
            # intentional here (unlike the approve route below).
            context = _field_context(store, field, diff_field("", field.text), readonly=False)
            return templates.TemplateResponse(request, "partials/linkedin_field.html", context)

    @app.post("/linkedin/fields/{field_id}/approve")
    def linkedin_approve_field(request: Request, field_id: int):
        with Session(app.state.engine) as db:
            store = LinkedinStore(db)
            existing = store.get_field(field_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"No LinkedIn field with id {field_id}")
            _ensure_latest(store, existing)
            try:
                field = store.approve_field(field_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            # Approving changes no text at all, so the real diff against the
            # previous version — the same one _linkedin_view computes — must
            # keep showing, unlike the edit route above.
            version = store.get(field.profile_id)
            previous_texts = _previous_texts(store, version)
            diff = diff_field(previous_texts.get((field.name, field.position), ""), field.text)
            context = _field_context(store, field, diff, readonly=False)
            return templates.TemplateResponse(request, "partials/linkedin_field.html", context)

    @app.get("/linkedin/versions/{profile_id}")
    def linkedin_version(request: Request, profile_id: int):
        with Session(app.state.engine) as db:
            version = LinkedinStore(db).get(profile_id)
            if version is None:
                raise HTTPException(status_code=404, detail="No such version")
            context = _linkedin_view(db, version, readonly=True)
            return templates.TemplateResponse(request, "linkedin.html", context)

    # --- counselor ---------------------------------------------------------

    def _chips_of(turn: Turn | None) -> list[str]:
        if not turn or not turn.action:
            return []
        visible = turn.action.get("then", turn.action) or {}
        return list(visible.get("quick_replies") or [])

    def _counselor_context(db: Session) -> dict:
        conversation = get_or_create_conversation(db)
        turns = recent_turns(db, conversation, limit=200)
        last = turns[-1] if turns else None
        last_action = (
            (last.action or {}).get("action") if last and last.role == "counselor" else None
        )
        chips = _chips_of(last if last and last.role == "counselor" else None)
        notes = open_notes(db, NOTE_KINDS)
        accepted = sum(1 for t in turns if t.proposal_state == "accepted")
        days = None
        if conversation.last_turn_at is not None:
            last_at = conversation.last_turn_at
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=UTC)
            days = (datetime.now(UTC) - last_at).days

        atom_ids = [t.atom_id for t in turns if t.atom_id is not None]
        atoms_by_turn = {}
        if atom_ids:
            by_id = {
                a.id: a
                for a in db.exec(select(EvidenceAtom).where(EvidenceAtom.id.in_(atom_ids))).all()
            }  # type: ignore[attr-defined]
            atoms_by_turn = {t.id: by_id[t.atom_id] for t in turns if t.atom_id in by_id}

        return {
            "turns": turns,
            "chips": chips,
            "ended": last_action == "end_sitting",
            "notes": notes,
            "accepted": accepted,
            "open_threads": sum(1 for n in notes if n.kind == "thread"),
            "days": days,
            "atoms_by_turn": atoms_by_turn,
        }

    def _reply_partial(request: Request, db: Session, reply):
        return templates.TemplateResponse(
            request,
            "partials/counselor_exchange.html",
            {
                "user_turn": reply.user_turn,
                "turn": reply.turn,
                "apology": reply.text if reply.turn is None else None,
                "chips": _chips_of(reply.turn),
                "ended": bool(
                    reply.turn and (reply.turn.action or {}).get("action") == "end_sitting"
                ),
            },
        )

    def _respond_or_error(db: Session, llm: LLMProvider, text: str | None):
        try:
            with _importer_errors():
                return respond(db, llm, text, config=config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/counselor")
    def counselor_page(request: Request, llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            conversation = get_or_create_conversation(db)
            spoken = db.exec(
                select(Turn).where(
                    Turn.conversation_id == conversation.id, Turn.role == "counselor"
                )
            ).first()
            if spoken is None:
                _respond_or_error(db, llm, None)
            return templates.TemplateResponse(request, "counselor.html", _counselor_context(db))

    @app.post("/counselor/say")
    def counselor_say(request: Request, text: str = Form(...), llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            reply = _respond_or_error(db, llm, text)
            return _reply_partial(request, db, reply)

    @app.post("/counselor/stop")
    def counselor_stop(request: Request, llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            return _reply_partial(
                request, db, _respond_or_error(db, llm, "I'd like to stop for today.")
            )

    @app.post("/counselor/continue")
    def counselor_continue(request: Request, llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            return _reply_partial(request, db, _respond_or_error(db, llm, None))

    @app.post("/counselor/proposals/{turn_id}/accept")
    def counselor_accept(
        request: Request,
        turn_id: int,
        title: str = Form(""),
        description: str = Form(""),
        skills: str = Form(""),
        metrics: str = Form(""),
    ):
        with Session(app.state.engine) as db:
            try:
                atom = accept_proposal(
                    db,
                    turn_id,
                    title=title.strip() or None,
                    description=description.strip() or None,
                    skills=[s.strip() for s in skills.split(",") if s.strip()]
                    if skills.strip()
                    else None,
                    metrics=metrics.strip() or None,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            turn = db.get(Turn, turn_id)
            return templates.TemplateResponse(
                request,
                "partials/counselor_turn.html",
                {"turn": turn, "atoms_by_turn": {turn_id: atom}},
            )

    @app.post("/counselor/proposals/{turn_id}/reject")
    def counselor_reject(request: Request, turn_id: int, llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            try:
                reject_proposal(db, turn_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            reply = _respond_or_error(db, llm, "Not quite — that isn't right.")
            rejected = db.get(Turn, turn_id)
            card = templates.get_template("partials/counselor_turn.html").render(
                turn=rejected, request=request, atoms_by_turn={}
            )
            exchange = _reply_partial(request, db, reply)
            # The rejected card replaces itself (hx-swap outerHTML on the card), and the
            # exchange rides along out-of-band so the transcript grows in the same response.
            # Relies on counselor_exchange.html opening with exactly <div id="exchange">; a mismatch leaves the transcript un-appended.
            body = card + exchange.body.decode("utf-8").replace(
                '<div id="exchange">', '<div id="transcript" hx-swap-oob="beforeend">', 1
            )
            return HTMLResponse(body)

    @app.post("/counselor/notes/{note_id}/close")
    def counselor_close_note(request: Request, note_id: int):
        with Session(app.state.engine) as db:
            note = db.get(CounselorNote, note_id)
            if note is None:
                raise HTTPException(status_code=404, detail="No such note")
            note.status = "closed"
            db.add(note)
            db.commit()
            return templates.TemplateResponse(
                request, "partials/counselor_notes.html", {"notes": open_notes(db, NOTE_KINDS)}
            )

    return app
