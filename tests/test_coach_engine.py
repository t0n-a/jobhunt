import pytest
from sqlmodel import Session

from jobhunt.coach.engine import Coach
from jobhunt.coach.models import Energy
from jobhunt.db import create_engine_for, init_db
from jobhunt.growth.gaps import Gap
from jobhunt.llm import FakeProvider
from jobhunt.profile.models import Provenance
from jobhunt.profile.store import AtomStore


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def gap() -> Gap:
    return Gap(skill="Kubernetes", job_count=14, weight=9.6, example_titles=["Platform Engineer"])


def quest_payload() -> dict:
    return {
        "title": "Deploy to k3s",
        "deliverable": "A public repo",
        "target_bullet": "Deployed a Python service to k3s with manifests.",
        "steps": [
            {
                "title": "Step one",
                "instructions": "Do the first thing.",
                "verification": {"type": "manual"},
            },
            {
                "title": "Step two",
                "instructions": "Do the second thing.",
                "verification": {"type": "manual"},
            },
        ],
    }


def coach(session, responses=None) -> Coach:
    return Coach(session, FakeProvider(responses or [quest_payload()]))


def test_no_quest_means_no_task(session):
    assert coach(session).current_task() is None


def test_starting_a_quest_makes_its_first_step_current(session):
    c = coach(session)

    c.start_quest(gap())

    assert c.current_task().title == "Step one"


def test_only_one_task_is_ever_visible(session):
    """The next step is hidden until the current one closes. There is no
    accessor returning a list of pending steps, and that absence is the design."""
    c = coach(session)
    c.start_quest(gap())

    assert not any(name for name in dir(c) if "all_tasks" in name or "list_tasks" in name)
    assert c.current_task().ordinal == 1


def test_today_reports_the_motivating_number_and_the_reward(session):
    """With ADHD an unexplained task reads as arbitrary; the reward shown upfront
    is what makes it feel worth starting."""
    c = coach(session)
    c.start_quest(gap())

    today = c.today(Energy.NORMAL)

    assert "14" in today["why"]
    assert "Deployed a Python service" in today["reward"]
    assert today["minutes"] == 45


def test_energy_changes_the_slice_not_the_quest(session):
    c = coach(session)
    c.start_quest(gap())

    assert c.today(Energy.LOW)["minutes"] == 15
    assert c.today(Energy.HIGH)["minutes"] == 120
    assert c.today(Energy.LOW)["step"].id == c.today(Energy.HIGH)["step"].id


def test_completing_a_step_advances_to_the_next(session):
    c = coach(session)
    c.start_quest(gap())

    c.complete_current(proof_note="Installed k3s.")

    assert c.current_task().title == "Step two"


def test_completing_a_step_writes_an_evidence_atom(session):
    """This is what makes the coach compound: every completed task makes the
    resume generator's material better."""
    c = coach(session)
    c.start_quest(gap())

    atom = c.complete_current(proof_note="Installed k3s and confirmed kubectl.")

    assert atom is not None
    assert atom.provenance is Provenance.QUEST
    assert atom.confirmed is False  # earned, but still the user's to review
    assert AtomStore(session).list(kind=atom.kind)


def test_finishing_every_step_closes_the_quest(session):
    c = coach(session)
    c.start_quest(gap())
    c.complete_current()
    c.complete_current()

    assert c.current_task() is None


def test_swap_replaces_the_current_step_without_losing_the_quest(session):
    """Avoidance must not escalate into quitting."""
    c = coach(session)
    c.start_quest(gap())
    first = c.current_task()

    replacement = c.swap_current()

    assert replacement is not None
    assert replacement.id != first.id
    assert c.current_task().id == replacement.id


def test_returning_after_a_lapse_costs_nothing(session):
    """No streak, no counter, no guilt copy — that pattern is what converts a
    lapse into abandonment."""
    c = coach(session)
    c.start_quest(gap())

    today = c.today(Energy.LOW)

    assert "streak" not in str(today).casefold()
    assert "missed" not in str(today).casefold()


def test_progress_only_counts_things_that_increase(session):
    c = coach(session)
    c.start_quest(gap())
    c.complete_current()

    progress = c.progress()

    assert progress["steps_done"] == 1
    assert progress["atoms_earned"] == 1
    assert "streak" not in progress
