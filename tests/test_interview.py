import pytest

from jobhunt.llm import FakeProvider, LLMError
from jobhunt.profile.interview import (
    HARVEST_PROMPT,
    MAX_TURNS,
    InterviewSession,
    harvest,
    next_question,
    record_answer,
)
from jobhunt.profile.models import AtomKind, Provenance


def test_first_question_is_generated_from_topic():
    session = InterviewSession(topic="URL shortener project")
    llm = FakeProvider(["What problem did the URL shortener solve?"])

    question = next_question(session, llm)

    assert question == "What problem did the URL shortener solve?"
    assert session.turns == [{"question": question, "answer": None}]
    assert "URL shortener project" in llm.prompts[0]


def test_next_question_requires_the_previous_one_to_be_answered():
    session = InterviewSession(topic="T", turns=[{"question": "Q1", "answer": None}])
    llm = FakeProvider(["Q2"])

    with pytest.raises(ValueError, match="unanswered"):
        next_question(session, llm)


def test_record_answer_fills_the_open_turn():
    session = InterviewSession(topic="T", turns=[{"question": "Q1", "answer": None}])

    record_answer(session, "It shortened URLs.")

    assert session.turns[0]["answer"] == "It shortened URLs."


def test_record_answer_with_no_open_turn_raises():
    session = InterviewSession(topic="T", turns=[{"question": "Q1", "answer": "A1"}])

    with pytest.raises(ValueError, match="no open question"):
        record_answer(session, "stray")


def test_follow_up_prompt_includes_prior_turns():
    session = InterviewSession(topic="T", turns=[{"question": "Q1", "answer": "A1"}])
    llm = FakeProvider(["Q2"])

    next_question(session, llm)

    assert "Q1" in llm.prompts[0]
    assert "A1" in llm.prompts[0]


def test_next_question_returns_none_at_turn_limit():
    turns = [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(MAX_TURNS)]
    session = InterviewSession(topic="T", turns=turns)
    llm = FakeProvider(["should not be used"])

    assert next_question(session, llm) is None
    assert llm.prompts == []


def test_harvest_builds_interview_atoms():
    session = InterviewSession(
        topic="URL shortener",
        turns=[{"question": "What did you build?", "answer": "A Flask URL shortener."}],
    )
    llm = FakeProvider(
        [
            {
                "atoms": [
                    {
                        "kind": "project",
                        "title": "URL shortener",
                        "description": "Flask service that shortens URLs.",
                        "skills": ["Python", "Flask"],
                        "metrics": None,
                    }
                ]
            }
        ]
    )

    atoms = harvest(session, llm)

    assert len(atoms) == 1
    assert atoms[0].provenance is Provenance.INTERVIEW
    assert atoms[0].kind is AtomKind.PROJECT
    assert atoms[0].confirmed is False
    assert session.status == "harvested"


def test_harvest_with_no_answered_turns_returns_empty():
    session = InterviewSession(topic="T", turns=[{"question": "Q1", "answer": None}])
    llm = FakeProvider([])

    assert harvest(session, llm) == []
    assert llm.prompts == []


def test_harvest_non_dict_response_raises_llm_error():
    session = InterviewSession(
        topic="T",
        turns=[{"question": "Q1", "answer": "A1"}],
    )
    llm = FakeProvider(["not a dict"])

    with pytest.raises(LLMError, match="Interview harvest expected a JSON object"):
        harvest(session, llm)


def test_failed_harvest_leaves_session_open():
    session = InterviewSession(
        topic="T",
        turns=[{"question": "Q1", "answer": "A1"}],
    )
    llm = FakeProvider(["not a dict"])

    with pytest.raises(LLMError, match="Interview harvest expected a JSON object"):
        harvest(session, llm)

    assert session.status == "open"


def test_harvest_is_idempotent():
    session = InterviewSession(
        topic="URL shortener",
        turns=[{"question": "What did you build?", "answer": "A Flask URL shortener."}],
    )
    llm = FakeProvider(
        [
            {
                "atoms": [
                    {
                        "kind": "project",
                        "title": "URL shortener",
                        "description": "Flask service that shortens URLs.",
                        "skills": ["Python", "Flask"],
                        "metrics": None,
                    }
                ]
            }
        ]
    )

    first_atoms = harvest(session, llm)
    assert len(first_atoms) == 1
    assert session.status == "harvested"
    assert len(llm.prompts) == 1

    second_atoms = harvest(session, llm)
    assert second_atoms == []
    assert len(llm.prompts) == 1


def test_full_interview_sequence_end_to_end():
    session = InterviewSession(topic="Distributed cache")
    llm = FakeProvider(
        [
            "What architecture did you choose?",
            "How did you handle invalidation?",
            {
                "atoms": [
                    {
                        "kind": "project",
                        "title": "Distributed cache",
                        "description": "Built distributed cache in Go.",
                        "skills": ["Go", "Distributed Systems"],
                        "metrics": "sub-millisecond latency",
                    }
                ]
            },
        ]
    )

    q1 = next_question(session, llm)
    assert q1 == "What architecture did you choose?"
    assert len(session.turns) == 1
    assert session.turns[0]["question"] == q1
    assert session.turns[0]["answer"] is None

    a1 = "Consistent hashing with virtual nodes in Go."
    record_answer(session, a1)
    assert len(session.turns) == 1
    assert session.turns[0]["question"] == q1
    assert session.turns[0]["answer"] == a1

    q2 = next_question(session, llm)
    assert q2 == "How did you handle invalidation?"
    assert len(session.turns) == 2
    assert session.turns[1]["question"] == q2
    assert session.turns[1]["answer"] is None

    a2 = "Lease-based TTLs with invalidation broadcasts."
    record_answer(session, a2)
    assert len(session.turns) == 2
    assert session.turns[1]["question"] == q2
    assert session.turns[1]["answer"] == a2

    atoms = harvest(session, llm)
    assert len(atoms) == 1
    assert atoms[0].title == "Distributed cache"
    assert atoms[0].provenance is Provenance.INTERVIEW
    assert atoms[0].source_ref == "Distributed cache"
    assert atoms[0].confirmed is False
    assert session.status == "harvested"

    assert len(llm.prompts) == 3
    harvest_prompt = llm.prompts[2]
    assert "Partial interview: 2 of 8 questions answered." in harvest_prompt
    assert f"Q: {q1}\nA: {a1}" in harvest_prompt
    assert f"Q: {q2}\nA: {a2}" in harvest_prompt


def test_harvest_prompt_constrains_skills():
    assert "leave it out rather than inferring it" in HARVEST_PROMPT
