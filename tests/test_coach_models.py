import pytest
from sqlmodel import Session, select

from jobhunt.coach.models import Energy, Quest, QuestStep, VerificationType
from jobhunt.db import create_engine_for, init_db


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def test_energy_maps_to_minutes():
    assert Energy.LOW.minutes == 15
    assert Energy.NORMAL.minutes == 45
    assert Energy.HIGH.minutes == 120


def test_quest_roundtrips(session):
    quest = Quest(
        skill="Kubernetes",
        title="Deploy jobhunt to a k3s cluster",
        deliverable="A public repo with working manifests",
        target_bullet="Deployed a Python service to a k3s cluster with manifests and a health check.",
    )
    session.add(quest)
    session.commit()
    session.refresh(quest)

    assert quest.id is not None
    assert quest.status == "active"


def test_step_stores_its_verification_spec_as_json(session):
    quest = Quest(skill="K8s", title="t", deliverable="d", target_bullet="b")
    session.add(quest)
    session.commit()
    session.refresh(quest)

    step = QuestStep(
        quest_id=quest.id,
        ordinal=1,
        title="Write the deployment manifest",
        instructions="Create deployment.yaml with a readiness probe.",
        verification={
            "type": VerificationType.GITHUB_COMMIT,
            "repo": "user/k8s-lab",
            "path_glob": "**/*.yaml",
        },
    )
    session.add(step)
    session.commit()
    session.refresh(step)

    stored = session.exec(select(QuestStep)).one()
    assert stored.verification["repo"] == "user/k8s-lab"
    assert stored.status == "pending"
    assert stored.atom_id is None


def test_no_streak_field_exists_anywhere():
    """The spec forbids a streak: a broken-streak screen is what converts a lapse
    into abandonment. Progress is derived from completed steps instead."""
    for model in (Quest, QuestStep):
        names = set(model.model_fields)
        assert not {n for n in names if "streak" in n.lower()}
        assert not {n for n in names if "missed" in n.lower()}
