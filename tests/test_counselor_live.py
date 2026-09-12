"""Three real turns against agy. Deselected by default (-m live)."""

import pytest
from sqlmodel import Session

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.counselor.engine import respond
from jobhunt.db import create_engine_for, init_db
from jobhunt.llm import build_provider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance

pytestmark = pytest.mark.live


def test_three_real_turns_each_return_a_visible_action(tmp_path):
    cfg = Config(
        profile=ProfileConfig(name="Ada", email="a@example.com", location="Porto"),
        llm=LLMConfig(cache_dir=tmp_path / "cache"),
        data_dir=tmp_path,
    )
    engine = create_engine_for(tmp_path / "db.sqlite")
    init_db(engine)
    llm = build_provider(cfg.llm)
    with Session(engine) as db:
        db.add(
            EvidenceAtom(
                kind=AtomKind.PROJECT,
                title="URL shortener",
                description="Flask and Redis.",
                skills=["Python"],
                provenance=Provenance.CV,
                confirmed=True,
            )
        )
        db.commit()
        opener = respond(db, llm, None, config=cfg)
        assert opener.turn is not None and opener.text
        second = respond(
            db, llm, "I built the shortener for a course; about 200 people used it.", config=cfg
        )
        assert second.turn is not None and second.text
        third = respond(
            db, llm, "I'd like to work on ML eventually, but I won't leave Porto.", config=cfg
        )
        assert third.turn is not None and third.text
