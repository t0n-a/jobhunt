import pytest

from jobhunt.config import ProfileConfig
from jobhunt.linkedin.compose import compose_profile
from jobhunt.llm import FakeProvider, LLMError
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance


def profile() -> ProfileConfig:
    return ProfileConfig(name="Ada", email="ada@example.com", location="Porto")


def atom(atom_id=1) -> EvidenceAtom:
    return EvidenceAtom(
        id=atom_id,
        kind=AtomKind.PROJECT,
        title="URL shortener",
        description="Built a link shortener in Flask, cached in Redis.",
        skills=["Python", "Flask", "Redis"],
        provenance=Provenance.INTERVIEW,
        confirmed=True,
    )


def response(**overrides) -> dict:
    payload = {
        "headline": "Backend Engineer",
        "about": "Python developer who builds small services.",
        "experience": [],
        "projects": [{"atom_id": 1, "title": "URL shortener", "text": "Built it in Flask."}],
        "skills": ["Python", "Flask"],
    }
    payload.update(overrides)
    return payload


def test_compose_returns_all_five_fields():
    llm = FakeProvider([response()])
    document = compose_profile([atom()], ["Python"], llm, profile=profile())

    assert document["headline"] == "Backend Engineer"
    assert document["about"].startswith("Python developer")
    assert document["skills"] == ["Python", "Flask"]
    assert document["experience"] == []
    assert document["projects"][0]["atom_id"] == 1
    assert document["projects"][0]["source"] == "model"


def test_the_prompt_carries_the_evidence_and_the_keywords():
    llm = FakeProvider([response()])
    compose_profile([atom()], ["Python", "Redis"], llm, profile=profile())

    prompt = llm.prompts[0]
    assert "id=1" in prompt  # the model must hand the id back
    assert "URL shortener" in prompt
    assert "Python, Redis" in prompt
    assert "220" in prompt  # the headline limit is stated, then checked in Python


def test_an_entry_without_a_traceable_atom_id_is_dropped():
    llm = FakeProvider([response(projects=[{"title": "Ghost", "text": "Did a thing."}])])
    document = compose_profile([atom()], [], llm, profile=profile())
    assert document["projects"] == []


def test_a_boolean_atom_id_is_not_an_atom_id():
    # A bool IS an int in Python, so a naive isinstance check stores True as an id.
    llm = FakeProvider([response(projects=[{"atom_id": True, "title": "X", "text": "Y"}])])
    document = compose_profile([atom()], [], llm, profile=profile())
    assert document["projects"] == []


def test_a_numeric_string_atom_id_is_accepted():
    llm = FakeProvider([response(projects=[{"atom_id": "1", "title": "X", "text": "Y"}])])
    document = compose_profile([atom()], [], llm, profile=profile())
    assert document["projects"][0]["atom_id"] == 1


def test_an_entry_with_no_text_is_dropped():
    llm = FakeProvider([response(projects=[{"atom_id": 1, "title": "X", "text": "   "}])])
    document = compose_profile([atom()], [], llm, profile=profile())
    assert document["projects"] == []


def test_a_non_object_response_is_an_llm_error():
    llm = FakeProvider(["not a dict"])
    with pytest.raises(LLMError, match="JSON object"):
        compose_profile([atom()], [], llm, profile=profile())


def test_a_response_with_no_usable_content_is_an_llm_error():
    # Persisting a version of five empty fields would look generated and be
    # worthless; the failure belongs at the model, where it happened.
    llm = FakeProvider([response(headline="", about="", projects=[], skills=[])])
    with pytest.raises(LLMError, match="no usable"):
        compose_profile([atom()], [], llm, profile=profile())


def test_skills_that_are_not_strings_are_ignored():
    llm = FakeProvider([response(skills=["Python", 7, "", None, "Redis"])])
    document = compose_profile([atom()], [], llm, profile=profile())
    assert document["skills"] == ["Python", "Redis"]
