import pytest
from sqlmodel import Session

from jobhunt.db import create_engine_for, init_db
from jobhunt.linkedin.models import LinkedinField, LinkedinProfile
from jobhunt.linkedin.store import LinkedinStore


@pytest.fixture
def store(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as session:
        yield LinkedinStore(session)


def make_version(store, *, headline="Backend Engineer") -> LinkedinProfile:
    return store.add_version(
        LinkedinProfile(keywords=["Python"], keyword_source="atoms", atom_ids=[1]),
        [
            LinkedinField(profile_id=0, name="headline", text=headline, position=0),
            LinkedinField(profile_id=0, name="about", text="Python developer.", position=0),
        ],
    )


def test_a_freshly_built_field_is_not_publishable(store):
    version = make_version(store)
    field = store.fields_for(version)[0]

    assert field.approved_at is None
    assert store.is_publishable(field) is False


def test_approving_makes_a_field_publishable(store):
    version = make_version(store)
    field = store.fields_for(version)[0]

    approved = store.approve_field(field.id)

    assert approved.approved_at is not None
    assert approved.approved_by == "user"
    assert store.is_publishable(approved) is True


def test_editing_does_not_approve(store):
    version = make_version(store)
    field = store.fields_for(version)[0]

    edited = store.edit_field(field.id, "Backend Engineer, Porto")

    assert edited.text == "Backend Engineer, Porto"
    assert edited.source == "user"
    assert edited.approved_at is None
    assert store.is_publishable(edited) is False


def test_editing_an_approved_field_withdraws_the_approval(store):
    # Otherwise approve-then-edit leaves approval standing on text nobody read,
    # which is the hole the gate exists to close.
    version = make_version(store)
    field = store.fields_for(version)[0]
    store.approve_field(field.id)

    edited = store.edit_field(field.id, "Something else entirely")

    assert edited.approved_at is None
    assert edited.approved_by is None
    assert store.is_publishable(edited) is False


def test_editing_recomputes_the_limit_flag(store):
    version = make_version(store)
    field = store.fields_for(version)[0]

    edited = store.edit_field(field.id, "x" * 221)
    assert edited.over_limit is True

    back = store.edit_field(field.id, "x" * 220)
    assert back.over_limit is False


def test_a_new_version_leaves_the_previous_versions_approvals_alone(store):
    first = make_version(store, headline="First")
    approved = store.approve_field(store.fields_for(first)[0].id)

    make_version(store, headline="Second")

    still = store.get_field(approved.id)
    assert still.approved_at is not None
    assert still.text == "First"


def test_a_new_versions_fields_start_unapproved(store):
    make_version(store, headline="First")
    second = make_version(store, headline="Second")

    assert all(not store.is_publishable(f) for f in store.fields_for(second))


def test_latest_returns_the_newest_version(store):
    make_version(store, headline="First")
    second = make_version(store, headline="Second")

    assert store.latest().id == second.id


def test_previous_returns_the_one_before(store):
    first = make_version(store, headline="First")
    second = make_version(store, headline="Second")

    assert store.previous(second).id == first.id
    assert store.previous(first) is None


def test_latest_is_none_when_nothing_has_been_built(store):
    assert store.latest() is None


def test_editing_an_unknown_field_raises(store):
    with pytest.raises(KeyError):
        store.edit_field(999, "text")


def test_approving_an_unknown_field_raises(store):
    with pytest.raises(KeyError):
        store.approve_field(999)
