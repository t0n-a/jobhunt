from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jobhunt.config import Config
from jobhunt.db import create_engine_for, init_db
from jobhunt.profile.store import AtomStore
from jobhunt.web.deps import get_store

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
        store: AtomStore = Depends(get_store),  # noqa: B008
    ):
        return templates.TemplateResponse(request, "atoms.html", {"atoms": store.list()})

    return app
