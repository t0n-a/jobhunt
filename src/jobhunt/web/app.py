import os
import shutil
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from jobhunt.coach.engine import Coach
from jobhunt.coach.models import Energy
from jobhunt.config import Config
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
from jobhunt.profile.interview import InterviewSession, harvest, next_question, record_answer
from jobhunt.profile.store import AtomStore
from jobhunt.tailor.models import Application, delete_bullet, edit_bullet, iter_bullets
from jobhunt.tailor.pipeline import mark_applied, render_application, tailor_job
from jobhunt.web.deps import get_llm, get_store

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
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/")
    def root():
        return RedirectResponse("/atoms")

    @app.get("/atoms")
    def atoms_page(
        request: Request,
        store: AtomStore = Depends(get_store),
    ):
        return templates.TemplateResponse(request, "atoms.html", {"atoms": store.list()})

    @app.get("/import")
    def import_page(request: Request):
        return templates.TemplateResponse(request, "import.html", {})

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
        return RedirectResponse("/atoms", status_code=303)

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
        return RedirectResponse("/atoms", status_code=303)

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
        return RedirectResponse("/atoms", status_code=303)

    @app.get("/interview")
    def interview_page(request: Request):
        with Session(app.state.engine) as session:
            sessions = session.exec(select(InterviewSession)).all()
        return templates.TemplateResponse(
            request, "interview.html", {"sessions": list(sessions), "current": None}
        )

    @app.post("/interview")
    def interview_start(
        request: Request,
        topic: str = Form(...),
        llm: LLMProvider = Depends(get_llm),
    ):
        with Session(app.state.engine) as db:
            interview = InterviewSession(topic=topic)
            with _importer_errors():
                next_question(interview, llm)
            db.add(interview)
            db.commit()
            db.refresh(interview)
            interview_id = interview.id
        return RedirectResponse(f"/interview/{interview_id}", status_code=303)

    @app.get("/interview/{interview_id}")
    def interview_detail(request: Request, interview_id: int):
        with Session(app.state.engine) as db:
            current = db.get(InterviewSession, interview_id)
            sessions = list(db.exec(select(InterviewSession)).all())
        if current is None:
            raise HTTPException(status_code=404, detail="No such interview")
        return templates.TemplateResponse(
            request, "interview.html", {"sessions": sessions, "current": current}
        )

    @app.post("/interview/{interview_id}/answer")
    def interview_answer(
        request: Request,
        interview_id: int,
        answer: str = Form(...),
        llm: LLMProvider = Depends(get_llm),
    ):
        with Session(app.state.engine) as db:
            interview = db.get(InterviewSession, interview_id)
            if interview is None:
                raise HTTPException(status_code=404, detail="No such interview")
            try:
                record_answer(interview, answer)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            db.add(interview)
            db.commit()
            db.refresh(interview)

            with _importer_errors():
                next_question(interview, llm)
            db.add(interview)
            db.commit()
            db.refresh(interview)
            return templates.TemplateResponse(
                request, "partials/interview_turn.html", {"current": interview}
            )

    @app.post("/interview/{interview_id}/harvest")
    def interview_harvest(
        interview_id: int,
        store: AtomStore = Depends(get_store),
        llm: LLMProvider = Depends(get_llm),
    ):
        with Session(app.state.engine) as db:
            interview = db.get(InterviewSession, interview_id)
            if interview is None:
                raise HTTPException(status_code=404, detail="No such interview")
            with _importer_errors():
                atoms = harvest(interview, llm)
            AtomStore(db).add_many(atoms)
            db.add(interview)
            db.commit()
        return RedirectResponse("/atoms", status_code=303)

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
                score = score_job(job, atoms, llm)
            db.add(score)
            db.commit()
        return RedirectResponse("/jobs", status_code=303)

    @app.post("/jobs/{job_id}/rescore")
    def job_rescore(job_id: int, llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            job = JobStore(db).get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="No such job")
            atoms = AtomStore(db).confirmed_only()
            with _importer_errors():
                score = score_job(job, atoms, llm)
            for old in db.exec(select(Score).where(Score.job_id == job_id)).all():
                db.delete(old)
            db.add(score)
            db.commit()
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

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
            _coach(db, llm).complete_current(proof_note or None)
        return RedirectResponse("/today", status_code=303)

    @app.post("/today/swap")
    def today_swap(llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            _coach(db, llm).swap_current()
        return RedirectResponse("/today", status_code=303)

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
        return RedirectResponse("/today", status_code=303)

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
        return RedirectResponse(f"/applications/{application_id}", status_code=303)

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
                render_application(
                    db, application, profile=config.profile, data_dir=config.data_dir
                )
        return RedirectResponse(f"/applications/{application_id}", status_code=303)

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
        return RedirectResponse("/applications", status_code=303)

    def _linkedin_view(db, version):
        """The page's data: each field with its limit, its diff and its state."""
        store = LinkedinStore(db)
        if version is None:
            return {"version": None, "fields": [], "dropped": []}
        previous = {
            (field.name, field.position): field.text
            for field in (
                store.fields_for(store.previous(version))
                if store.previous(version) is not None
                else []
            )
        }
        fields = []
        for field in store.fields_for(version):
            spec = field_spec(field.name)
            fields.append(
                {
                    "field": field,
                    "spec": spec,
                    "diff": diff_field(previous.get((field.name, field.position), ""), field.text),
                    "publishable": store.is_publishable(field),
                    "limit_message": over_limit_message(spec, field.text)
                    if field.over_limit
                    else "",
                }
            )
        return {"version": version, "fields": fields, "dropped": version.dropped}

    @app.get("/linkedin")
    def linkedin_page(request: Request):
        with Session(app.state.engine) as db:
            context = _linkedin_view(db, LinkedinStore(db).latest())
            return templates.TemplateResponse(request, "linkedin.html", context)

    @app.post("/linkedin/generate")
    def linkedin_generate(request: Request, llm: LLMProvider = Depends(get_llm)):
        with Session(app.state.engine) as db:
            try:
                build_profile(db, llm, profile=config.profile)
            except EmptyProfileError as exc:
                # Not a 502: this is the user's own store being empty, and the
                # page can say what to do about it. Caught BEFORE _importer_errors,
                # which would map it to a provider fault.
                context = _linkedin_view(db, LinkedinStore(db).latest())
                context["refusal"] = str(exc)
                return templates.TemplateResponse(request, "linkedin.html", context)
            except LLMError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        return RedirectResponse("/linkedin", status_code=303)

    @app.post("/linkedin/fields/{field_id}")
    def linkedin_edit_field(request: Request, field_id: int, text: str = Form(...)):
        with Session(app.state.engine) as db:
            store = LinkedinStore(db)
            try:
                field = store.edit_field(field_id, text)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            spec = field_spec(field.name)
            return templates.TemplateResponse(
                request,
                "partials/linkedin_field.html",
                {
                    "field": field,
                    "spec": spec,
                    "diff": diff_field("", field.text),
                    "publishable": store.is_publishable(field),
                    "limit_message": over_limit_message(spec, field.text)
                    if field.over_limit
                    else "",
                },
            )

    @app.post("/linkedin/fields/{field_id}/approve")
    def linkedin_approve_field(request: Request, field_id: int):
        with Session(app.state.engine) as db:
            store = LinkedinStore(db)
            try:
                field = store.approve_field(field_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            spec = field_spec(field.name)
            return templates.TemplateResponse(
                request,
                "partials/linkedin_field.html",
                {
                    "field": field,
                    "spec": spec,
                    "diff": diff_field("", field.text),
                    "publishable": store.is_publishable(field),
                    "limit_message": over_limit_message(spec, field.text)
                    if field.over_limit
                    else "",
                },
            )

    @app.get("/linkedin/versions/{profile_id}")
    def linkedin_version(request: Request, profile_id: int):
        with Session(app.state.engine) as db:
            version = LinkedinStore(db).get(profile_id)
            if version is None:
                raise HTTPException(status_code=404, detail="No such version")
            return templates.TemplateResponse(request, "linkedin.html", _linkedin_view(db, version))

    return app
