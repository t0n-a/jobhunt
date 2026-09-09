import pytest
from sqlmodel import Session

from jobhunt.db import create_engine_for, init_db
from jobhunt.linkedin.keywords import target_keywords
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore
from jobhunt.tailor.models import Application


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def seed_application(session, keywords):
    session.add(Application(job_id=1, keywords=keywords))
    session.commit()


def seed_atom(session, skills, *, confirmed=True):
    AtomStore(session).add(
        EvidenceAtom(
            kind=AtomKind.PROJECT,
            title="Shortener",
            description="Built one.",
            skills=skills,
            provenance=Provenance.INTERVIEW,
            confirmed=confirmed,
        )
    )


def test_keywords_come_from_applications_when_there_are_any(session):
    seed_application(session, ["Python", "Redis"])
    seed_application(session, ["Python", "Django"])
    seed_atom(session, ["Rust"])

    keywords, source = target_keywords(session)

    assert source == "applications"
    # Most frequent first: Python appears in both applications.
    assert keywords[0] == "Python"
    assert set(keywords) == {"Python", "Redis", "Django"}
    # The atom store is NOT consulted on this path.
    assert "Rust" not in keywords


def test_keywords_fall_back_to_confirmed_atom_skills(session):
    seed_atom(session, ["Python", "Redis"])
    seed_atom(session, ["Python"])

    keywords, source = target_keywords(session)

    assert source == "atoms"
    assert keywords[0] == "Python"


def test_the_fallback_ignores_unconfirmed_atoms(session):
    seed_atom(session, ["Python"], confirmed=True)
    seed_atom(session, ["Kubernetes"], confirmed=False)

    keywords, _ = target_keywords(session)

    assert "Kubernetes" not in keywords


def test_an_empty_store_yields_no_keywords(session):
    assert target_keywords(session) == ([], "atoms")


def test_the_limit_truncates_the_result(session):
    seed_application(session, [f"kw{i}" for i in range(40)])
    keywords, _ = target_keywords(session, limit=5)
    assert len(keywords) == 5


def test_applications_with_no_keywords_do_not_count_as_a_source(session):
    # An application whose keyword extraction returned nothing must not
    # strand the profile on an empty "applications" derivation.
    seed_application(session, [])
    seed_atom(session, ["Python"])

    keywords, source = target_keywords(session)

    assert source == "atoms"
    assert keywords == ["Python"]
