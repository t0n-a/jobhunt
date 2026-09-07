from datetime import date

import pytest
from sqlmodel import Session

from jobhunt.db import create_engine_for, init_db
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore


@pytest.fixture
def store(tmp_path):
    engine = create_engine_for(tmp_path / "test.db")
    init_db(engine)
    with Session(engine) as session:
        yield AtomStore(session)


def make_atom(**overrides) -> EvidenceAtom:
    defaults = {
        "kind": AtomKind.PROJECT,
        "title": "Built a URL shortener",
        "description": "Flask service with Redis caching, deployed on a VPS.",
        "skills": ["Python", "Flask", "Redis"],
        "provenance": Provenance.INTERVIEW,
    }
    defaults.update(overrides)
    return EvidenceAtom(**defaults)


def test_add_assigns_id_and_roundtrips_skills(store: AtomStore):
    atom = store.add(make_atom())

    assert atom.id is not None
    fetched = store.get(atom.id)
    assert fetched is not None
    assert fetched.skills == ["Python", "Flask", "Redis"]
    assert fetched.kind is AtomKind.PROJECT


def test_new_atoms_are_unconfirmed_by_default(store: AtomStore):
    atom = store.add(make_atom())

    assert atom.confirmed is False


def test_list_filters_by_kind(store: AtomStore):
    store.add(make_atom(kind=AtomKind.PROJECT))
    store.add(make_atom(kind=AtomKind.COURSE, title="Claude Code course"))

    courses = store.list(kind=AtomKind.COURSE)

    assert len(courses) == 1
    assert courses[0].title == "Claude Code course"


def test_list_filters_by_confirmed(store: AtomStore):
    a = store.add(make_atom())
    store.add(make_atom(title="Second"))
    store.update(a.id, confirmed=True)

    assert len(store.list(confirmed=True)) == 1
    assert len(store.list(confirmed=False)) == 1


def test_list_filters_by_skill_case_insensitively(store: AtomStore):
    store.add(make_atom(skills=["Python", "Docker"]))
    store.add(make_atom(title="Other", skills=["Java"]))

    assert len(store.list(skill="python")) == 1
    assert len(store.list(skill="java")) == 1
    assert len(store.list(skill="rust")) == 0


def test_update_changes_fields_and_persists(store: AtomStore):
    atom = store.add(make_atom())

    updated = store.update(atom.id, title="Renamed", skills=["Go"])

    assert updated.title == "Renamed"
    assert store.get(atom.id).skills == ["Go"]


def test_update_unknown_id_raises(store: AtomStore):
    with pytest.raises(KeyError):
        store.update(999, title="nope")


def test_delete_removes_atom(store: AtomStore):
    atom = store.add(make_atom())

    store.delete(atom.id)

    assert store.get(atom.id) is None


def test_skill_counts_aggregates_across_atoms(store: AtomStore):
    store.add(make_atom(skills=["Python", "Docker"]))
    store.add(make_atom(title="Two", skills=["Python"]))

    counts = store.skill_counts()

    assert counts["Python"] == 2
    assert counts["Docker"] == 1


def test_add_many_inserts_all(store: AtomStore):
    atoms = store.add_many([make_atom(), make_atom(title="Two")])

    assert len(atoms) == 2
    assert len(store.list()) == 2


def test_dates_roundtrip(store: AtomStore):
    atom = store.add(make_atom(started_on=date(2025, 9, 1), ended_on=date(2026, 6, 30)))

    assert store.get(atom.id).started_on == date(2025, 9, 1)


def test_delete_unknown_id_is_a_noop(store: AtomStore):
    a1 = store.add(make_atom(title="First"))
    a2 = store.add(make_atom(title="Second"))

    store.delete(999)

    atoms = store.list()
    assert len(atoms) == 2
    assert {a.id for a in atoms} == {a1.id, a2.id}


def test_skill_counts_counts_each_skill_once_per_atom(store: AtomStore):
    store.add(make_atom(skills=["Python", "Python", "Docker"]))

    counts = store.skill_counts()

    assert counts["Python"] == 1
    assert counts["Docker"] == 1


def test_atoms_persist_across_sessions(tmp_path):
    engine = create_engine_for(tmp_path / "test.db")
    init_db(engine)

    with Session(engine) as session1:
        store1 = AtomStore(session1)
        atom = store1.add(
            EvidenceAtom(
                kind=AtomKind.PROJECT,
                title="Cross Session Project",
                description="Testing persistence across separate sessions.",
                skills=["Python", "Rust"],
                provenance=Provenance.CV,
                started_on=date(2025, 1, 1),
                ended_on=date(2025, 6, 1),
            )
        )
        atom_id = atom.id

    with Session(engine) as session2:
        store2 = AtomStore(session2)
        atom = store2.get(atom_id)
        assert atom is not None
        assert atom.skills == ["Python", "Rust"]
        assert atom.kind is AtomKind.PROJECT
        assert atom.started_on == date(2025, 1, 1)
        assert atom.ended_on == date(2025, 6, 1)
