import pytest

from jobhunt.counselor.actions import (
    ACTION_SCHEMA,
    Ask,
    AtomDraft,
    EndSitting,
    LookUp,
    Note,
    ProposeAtom,
    action_to_dict,
    parse_action,
    visible_text,
)
from jobhunt.llm import LLMError


def test_ask_parses_and_trims_quick_replies():
    action = parse_action(
        {
            "action": "ask",
            "text": "Solo or team?",
            "quick_replies": ["Solo", "Team of 2-4", "Larger", "x" * 41, "fifth"],
        }
    )
    assert action == Ask("Solo or team?", ("Solo", "Team of 2-4", "Larger"))
    assert visible_text(action) == "Solo or team?"


def test_look_up_keeps_tool_and_args():
    assert parse_action({"action": "look_up", "tool": "repo", "args": {"name": "x"}}) == LookUp(
        "repo", {"name": "x"}
    )


def test_propose_atom_builds_a_draft():
    action = parse_action(
        {
            "action": "propose_atom",
            "text": "I'd record this:",
            "atom": {"kind": "project", "title": "T", "description": "D", "skills": ["Python"]},
            "because": "you said 'I built T'",
        }
    )
    assert action == ProposeAtom(
        "I'd record this:",
        AtomDraft("project", "T", "D", ("Python",), None),
        "you said 'I built T'",
    )


def test_note_wraps_a_visible_action():
    action = parse_action(
        {
            "action": "note",
            "kind": "goal",
            "text": "wants ML",
            "close": False,
            "then": {"action": "ask", "text": "Why ML?"},
        }
    )
    assert action == Note("goal", "wants ML", False, Ask("Why ML?"))
    assert visible_text(action) == "Why ML?"


def test_end_sitting_optional_next_time():
    assert parse_action({"action": "end_sitting", "summary": "Good stop."}) == EndSitting(
        "Good stop.", None
    )


@pytest.mark.parametrize(
    "raw",
    [
        "not a dict",
        {"action": "dance"},
        {"action": "ask"},
        {"action": "note", "kind": "mood", "text": "x", "then": {"action": "ask", "text": "y"}},
        {
            "action": "note",
            "kind": "goal",
            "text": "x",
            "then": {"action": "look_up", "tool": "atom", "args": {}},
        },
        {
            "action": "propose_atom",
            "text": "t",
            "atom": {"kind": "wish", "title": "T", "description": "D", "skills": []},
            "because": "b",
        },
    ],
)
def test_bad_shapes_raise_llm_error(raw):
    with pytest.raises(LLMError):
        parse_action(raw)


def test_action_to_dict_round_trips():
    for action in [
        Ask("q", ("a",)),
        LookUp("gap", {"skill": "Docker"}),
        ProposeAtom("t", AtomDraft("course", "T", "D", (), "3"), "b"),
        Note("thread", "ask about X", True, Ask("q")),
        EndSitting("s", "n"),
    ]:
        assert parse_action(action_to_dict(action)) == action


def test_schema_names_every_action():
    assert ACTION_SCHEMA["properties"]["action"]["enum"] == [
        "ask",
        "look_up",
        "propose_atom",
        "note",
        "end_sitting",
    ]
