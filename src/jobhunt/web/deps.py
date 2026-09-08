"""FastAPI dependencies. Overridden in tests via app.dependency_overrides."""

from fastapi import Request
from sqlmodel import Session

from jobhunt.llm import build_provider
from jobhunt.profile.store import AtomStore


def get_store(request: Request):
    engine = request.app.state.engine
    with Session(engine) as session:
        yield AtomStore(session)


def get_llm(request: Request):
    """One provider per app, not per request.

    Scoring a batch spawns one subprocess per posting otherwise.
    """
    app = request.app
    if getattr(app.state, "llm", None) is None:
        app.state.llm = build_provider(app.state.config.llm)
    return app.state.llm
