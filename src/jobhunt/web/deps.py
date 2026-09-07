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
    return build_provider(request.app.state.config.llm)
