import os
import shutil
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from jobhunt.config import Config
from jobhunt.db import create_engine_for, init_db
from jobhunt.llm import LLMError, LLMProvider
from jobhunt.profile.importers.coursework import import_coursework
from jobhunt.profile.importers.cv import import_cv
from jobhunt.profile.importers.github import import_github
from jobhunt.profile.interview import InterviewSession, harvest, next_question, record_answer
from jobhunt.profile.store import AtomStore
from jobhunt.web.deps import get_llm, get_store

HERE = Path(__file__).parent


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
        uploads = config.data_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        filename = Path(file.filename).name if file.filename else "upload"
        target = uploads / filename
        with target.open("wb") as out:
            shutil.copyfileobj(file.file, out)

        try:
            atoms = import_cv(target, llm)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        store.add_many(atoms)
        return RedirectResponse("/atoms", status_code=303)

    @app.post("/import/github")
    def import_github_route(
        username: str = Form(...),
        store: AtomStore = Depends(get_store),
        llm: LLMProvider = Depends(get_llm),
    ):
        try:
            atoms = import_github(username, llm, token=os.environ.get("GITHUB_TOKEN"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
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
        try:
            atoms = import_coursework(text, institution, llm)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

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
            record_answer(interview, answer)
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
            try:
                atoms = harvest(interview, llm)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except LLMError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            db.add(interview)
            db.commit()
        store.add_many(atoms)
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

    return app
