import pytest
from sqlmodel import Session

from jobhunt.config import ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.linkedin.build import build_profile
from jobhunt.linkedin.store import LinkedinStore
from jobhunt.llm import FakeProvider
from jobhunt.matching.score import EmptyProfileError
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def profile() -> ProfileConfig:
    return ProfileConfig(name="Ada", email="ada@example.com", location="Porto")


def seed_atom(session, *, confirmed=True) -> EvidenceAtom:
    return AtomStore(session).add(
        EvidenceAtom(
            kind=AtomKind.PROJECT,
            title="URL shortener",
            description="Built a link shortener in Flask, cached in Redis.",
            skills=["Python", "Flask", "Redis"],
            provenance=Provenance.INTERVIEW,
            confirmed=confirmed,
        )
    )


def response(atom_id, **overrides) -> dict:
    payload = {
        "headline": "Backend Engineer",
        "about": "Python developer who built a Flask service cached in Redis.",
        "experience": [],
        "projects": [{"atom_id": atom_id, "title": "URL shortener", "text": "Built it in Flask."}],
        "skills": ["Python", "Flask"],
    }
    payload.update(overrides)
    return payload


def fields_by_name(session, version) -> dict:
    return {f.name: f for f in LinkedinStore(session).fields_for(version)}


def test_a_build_persists_one_unapproved_version(session):
    atom = seed_atom(session)
    llm = FakeProvider([response(atom.id)])

    version = build_profile(session, llm, profile=profile())

    store = LinkedinStore(session)
    fields = store.fields_for(version)
    assert version.id is not None
    assert version.atom_ids == [atom.id]
    assert {f.name for f in fields} >= {"headline", "about", "projects", "skills"}
    assert all(not store.is_publishable(f) for f in fields)


def test_an_empty_atom_store_is_refused_before_any_model_call(session):
    llm = FakeProvider([])  # any call at all would raise "ran out of responses"

    with pytest.raises(EmptyProfileError, match="confirm"):
        build_profile(session, llm, profile=profile())

    assert llm.prompts == []
    assert LinkedinStore(session).latest() is None


def test_unconfirmed_atoms_do_not_count_as_evidence(session):
    seed_atom(session, confirmed=False)
    llm = FakeProvider([])

    with pytest.raises(EmptyProfileError):
        build_profile(session, llm, profile=profile())


def test_a_fabricated_number_in_the_about_is_stripped_and_reported(session):
    atom = seed_atom(session)
    llm = FakeProvider([response(atom.id, about="Served 90000 monthly users.")])

    version = build_profile(session, llm, profile=profile())

    about = fields_by_name(session, version)["about"]
    assert about.text == ""
    assert any(d["field"] == "about" and "90000" in d["reason"] for d in version.dropped)


def test_a_fabricated_technology_in_an_entry_is_stripped(session):
    atom = seed_atom(session)
    llm = FakeProvider(
        [
            response(
                atom.id,
                projects=[
                    {"atom_id": atom.id, "title": "Shortener", "text": "Ran it on Kubernetes."}
                ],
            )
        ]
    )

    version = build_profile(session, llm, profile=profile())

    names = {f.name for f in LinkedinStore(session).fields_for(version)}
    assert "projects" not in names
    assert any("Kubernetes" in d["reason"] for d in version.dropped)


def test_an_unevidenced_skill_is_dropped_from_the_skills_line(session):
    atom = seed_atom(session)
    llm = FakeProvider([response(atom.id, skills=["Python", "Kubernetes"])])

    version = build_profile(session, llm, profile=profile())

    skills = fields_by_name(session, version)["skills"]
    assert skills.text == "Python"
    assert any("Kubernetes" in d["text"] for d in version.dropped)


def test_an_over_length_field_is_flagged_and_not_truncated(session):
    atom = seed_atom(session)
    long_headline = "Python " * 60  # well over 220 characters, all evidenced
    llm = FakeProvider([response(atom.id, headline=long_headline)])

    version = build_profile(session, llm, profile=profile())

    headline = fields_by_name(session, version)["headline"]
    assert headline.over_limit is True
    assert headline.text == long_headline.strip()  # NOT cut


def test_an_entry_gets_one_row_per_entry_with_its_atom_id(session):
    atom = seed_atom(session)
    llm = FakeProvider(
        [
            response(
                atom.id,
                projects=[
                    {"atom_id": atom.id, "title": "Shortener", "text": "Built it in Flask."},
                    {"atom_id": atom.id, "title": "Cache", "text": "Cached it in Redis."},
                ],
            )
        ]
    )

    version = build_profile(session, llm, profile=profile())

    projects = [f for f in LinkedinStore(session).fields_for(version) if f.name == "projects"]
    assert len(projects) == 2
    assert [f.position for f in projects] == [0, 1]
    assert all(f.atom_id == atom.id for f in projects)
    assert projects[0].entry_title == "Shortener"


def test_a_failed_model_call_leaves_no_version_at_all(session):
    seed_atom(session)
    llm = FakeProvider(["not a dict"])

    with pytest.raises(Exception):  # noqa: B017 - build_profile's failure mode is deliberately unspecified here
        build_profile(session, llm, profile=profile())

    assert LinkedinStore(session).latest() is None


def test_the_keyword_source_is_recorded_on_the_version(session):
    atom = seed_atom(session)
    llm = FakeProvider([response(atom.id)])

    version = build_profile(session, llm, profile=profile())

    assert version.keyword_source == "atoms"
    assert version.keywords  # derived from the atom's skills


def test_a_shortlist_that_matches_nothing_still_builds(session):
    # Keywords come from applications; an application for a role that shares no
    # skill with the user's evidence leaves shortlist_atoms with nothing to rank.
    # Refusing here would fail exactly the user who most needs a profile.
    from jobhunt.tailor.models import Application

    atom = seed_atom(session)  # Python / Flask / Redis
    session.add(Application(job_id=1, keywords=["Rust", "Erlang"]))
    session.commit()
    llm = FakeProvider([response(atom.id)])

    version = build_profile(session, llm, profile=profile())

    assert version.id is not None
    assert version.keyword_source == "applications"
    assert version.atom_ids == [atom.id]
