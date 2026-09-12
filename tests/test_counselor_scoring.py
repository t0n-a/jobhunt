import pytest
from sqlmodel import Session

from jobhunt.config import ProfileConfig
from jobhunt.counselor.models import Conversation, CounselorNote
from jobhunt.counselor.scoring import goals_block, render_for_scoring, render_goals, scoring_block
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job
from jobhunt.linkedin.compose import compose_profile
from jobhunt.llm import FakeProvider
from jobhunt.matching.score import PROMPT, score_job
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance


def note(kind, text, status="open"):
    return CounselorNote(conversation_id=1, kind=kind, text=text, status=status)


def test_render_for_scoring_uses_goals_and_constraints_only():
    block = render_for_scoring(
        [
            note("goal", "wants ML"),
            note("constraint", "no relocation from Porto"),
            note("preference", "small teams"),
            note("thread", "ask X"),
        ]
    )
    assert block == "Candidate goals: wants ML.\nCandidate constraints: no relocation from Porto."
    assert render_for_scoring([note("preference", "x")]) == ""
    assert (
        render_goals([note("goal", "a"), note("goal", "b"), note("constraint", "c")])
        == "Candidate goals: a; b."
    )


@pytest.fixture
def db(tmp_path):
    engine = create_engine_for(tmp_path / "db.sqlite")
    init_db(engine)
    with Session(engine) as session:
        conv = Conversation()
        session.add(conv)
        session.commit()
        session.add(CounselorNote(conversation_id=conv.id, kind="goal", text="wants ML"))
        session.add(
            CounselorNote(
                conversation_id=conv.id, kind="constraint", text="closed", status="closed"
            )
        )
        session.commit()
        yield session


def test_blocks_read_only_open_notes(db):
    assert scoring_block(db) == "Candidate goals: wants ML."
    assert goals_block(db) == "Candidate goals: wants ML."


SCORE = {
    "fit_score": 50,
    "matched_skills": [],
    "missing_skills": [],
    "red_flags": [],
    "seniority_fit": "match",
    "track": "backend",
    "rationale": "r",
}


def test_score_job_appends_the_block_and_the_rule():
    atoms = [
        EvidenceAtom(
            id=1,
            kind=AtomKind.PROJECT,
            title="T",
            description="D",
            skills=["Python"],
            provenance=Provenance.CV,
            confirmed=True,
        )
    ]
    job = Job(id=1, title="J", company="C", description="d", url="", content_hash="h")
    llm = FakeProvider([SCORE, SCORE])
    score_job(job, atoms, llm, notes="Candidate constraints: no on-site in Lisbon.")
    score_job(job, atoms, llm)
    assert "Candidate constraints: no on-site in Lisbon." in llm.prompts[0]
    assert "Candidate constraints" not in llm.prompts[1]
    assert (
        "Lower fit_score for a job that violates a stated constraint. Do not raise fit_score for a goal — goals decide track, not fit."
        in PROMPT
    )


def test_compose_profile_receives_goals():
    atoms = [
        EvidenceAtom(
            id=1,
            kind=AtomKind.PROJECT,
            title="T",
            description="D",
            skills=["Python"],
            provenance=Provenance.CV,
            confirmed=True,
        )
    ]
    llm = FakeProvider(
        [{"headline": "h", "about": "a", "experience": [], "projects": [], "skills": ["Python"]}]
    )
    compose_profile(
        atoms,
        ["Python"],
        llm,
        profile=ProfileConfig(name="Ada", email="a@x", location="Porto"),
        goals="Candidate goals: wants ML.",
    )
    assert "Candidate goals: wants ML." in llm.prompts[0]
