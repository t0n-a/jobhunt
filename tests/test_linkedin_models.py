import pytest
from sqlmodel import Session, select

from jobhunt.db import create_engine_for, init_db
from jobhunt.linkedin.models import LinkedinField, LinkedinProfile


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def test_init_db_creates_both_linkedin_tables(session):
    # A table absent from SQLModel.metadata is a table create_all never makes,
    # and the failure surfaces as a confusing OperationalError much later.
    assert session.exec(select(LinkedinProfile)).all() == []
    assert session.exec(select(LinkedinField)).all() == []


def test_a_version_defaults_to_empty_json_columns(session):
    version = LinkedinProfile()
    session.add(version)
    session.commit()
    session.refresh(version)
    assert version.keywords == []
    assert version.atom_ids == []
    assert version.dropped == []
    assert version.keyword_source == "atoms"
    assert version.created_at is not None


def test_a_field_starts_unapproved(session):
    version = LinkedinProfile()
    session.add(version)
    session.commit()
    session.refresh(version)

    field = LinkedinField(profile_id=version.id, name="headline", text="Backend Engineer")
    session.add(field)
    session.commit()
    session.refresh(field)

    # The gate's default. A field that arrived approved would make the gate
    # something callers have to remember to apply.
    assert field.approved_at is None
    assert field.approved_by is None
    assert field.source == "model"
    assert field.over_limit is False


def test_json_columns_round_trip(session):
    version = LinkedinProfile(
        keywords=["Python", "Redis"],
        keyword_source="applications",
        atom_ids=[3, 7],
        dropped=[{"field": "about", "text": "Led 40 engineers", "reason": "States 40."}],
    )
    session.add(version)
    session.commit()
    session.refresh(version)

    assert version.keywords == ["Python", "Redis"]
    assert version.atom_ids == [3, 7]
    assert version.dropped[0]["reason"] == "States 40."
