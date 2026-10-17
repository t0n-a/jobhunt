import pytest
from sqlmodel import Session

from jobhunt.db import create_engine_for, init_db
from jobhunt.state import get_state, is_paused, set_paused, set_state


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def test_state_round_trips_and_overwrites(session):
    assert get_state(session, "k") is None
    assert get_state(session, "k", "d") == "d"
    set_state(session, "k", "1")
    set_state(session, "k", "2")
    assert get_state(session, "k") == "2"


def test_pause_defaults_off(session):
    assert is_paused(session) is False
    set_paused(session, True)
    assert is_paused(session) is True
