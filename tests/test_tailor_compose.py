import pytest

from jobhunt.config import ProfileConfig
from jobhunt.jobs.models import Job, posting_hash
from jobhunt.llm import FakeProvider, LLMError
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.tailor.compose import compose_cover_letter, compose_resume


def profile() -> ProfileConfig:
    return ProfileConfig(name="Ada Lovelace", email="ada@example.com", location="Porto")


def job() -> Job:
    description = "We need Python and Kubernetes."
    return Job(
        title="Platform Engineer",
        company="Acme",
        description=description,
        url="u",
        content_hash=posting_hash("Platform Engineer", "Acme", description),
    )


def atom(atom_id, title="URL shortener") -> EvidenceAtom:
    return EvidenceAtom(
        id=atom_id,
        kind=AtomKind.PROJECT,
        title=title,
        description="Built a link shortener in Flask, cached in Redis.",
        skills=["Python", "Flask", "Redis"],
        provenance=Provenance.INTERVIEW,
        confirmed=True,
    )


def payload(**over) -> dict:
    doc = {
        "headline": "Backend Engineer",
        "summary": "Recent graduate with Python services in production.",
        "sections": [
            {
                "name": "Projects",
                "entries": [
                    {
                        "atom_id": 1,
                        "title": "URL shortener",
                        "org": "Personal",
                        "dates": "2026",
                        "bullets": [
                            {"atom_id": 1, "text": "Built a link shortener in Flask."},
                            {"atom_id": 1, "text": "Cached responses in Redis."},
                        ],
                    }
                ],
            }
        ],
        "skills": ["Python", "Flask", "Redis"],
    }
    doc.update(over)
    return doc


def test_compose_returns_the_document():
    llm = FakeProvider([payload()])

    doc = compose_resume(job(), [atom(1)], ["Python"], llm, profile=profile())

    assert doc["headline"] == "Backend Engineer"
    assert doc["sections"][0]["entries"][0]["title"] == "URL shortener"


def test_every_bullet_is_stamped_as_model_written():
    """The verifier checks model bullets and exempts user bullets, so the
    provenance of each line has to be recorded the moment it is created."""
    llm = FakeProvider([payload()])

    doc = compose_resume(job(), [atom(1)], ["Python"], llm, profile=profile())

    sources = [b["source"] for b in doc["sections"][0]["entries"][0]["bullets"]]
    assert sources == ["model", "model"]


def test_a_bullet_without_an_atom_id_is_dropped():
    """A bullet with no traceable source cannot be verified against anything, so
    it must not reach the document at all."""
    doc = payload()
    doc["sections"][0]["entries"][0]["bullets"].append({"text": "Led the company."})
    llm = FakeProvider([doc])

    composed = compose_resume(job(), [atom(1)], ["Python"], llm, profile=profile())

    texts = [b["text"] for b in composed["sections"][0]["entries"][0]["bullets"]]
    assert "Led the company." not in texts


def test_the_shortlist_reaches_the_prompt_with_its_ids():
    llm = FakeProvider([payload()])

    compose_resume(job(), [atom(7, title="Rate limiter")], ["Python"], llm, profile=profile())

    assert "Rate limiter" in llm.prompts[0]
    assert "7" in llm.prompts[0]


def test_the_keywords_reach_the_prompt():
    llm = FakeProvider([payload()])

    compose_resume(job(), [atom(1)], ["Kubernetes"], llm, profile=profile())

    assert "Kubernetes" in llm.prompts[0]


def test_the_prompt_forbids_inventing_facts():
    llm = FakeProvider([payload()])

    compose_resume(job(), [atom(1)], ["Python"], llm, profile=profile())

    assert "invent" in llm.prompts[0].casefold()


def test_a_non_dict_response_raises_llm_error():
    with pytest.raises(LLMError):
        compose_resume(job(), [atom(1)], ["Python"], FakeProvider(["nope"]), profile=profile())


def test_a_document_with_no_bullets_raises_llm_error():
    """An empty resume is not a resume; failing loudly beats rendering a blank
    PDF the user might send."""
    empty = payload(sections=[])

    with pytest.raises(LLMError):
        compose_resume(job(), [atom(1)], ["Python"], FakeProvider([empty]), profile=profile())


def test_cover_letter_is_returned_as_prose():
    llm = FakeProvider([{"cover_letter": "Dear Acme,\n\nI built a link shortener."}])

    letter = compose_cover_letter(
        job(),
        [{"atom_id": 1, "text": "Built a link shortener in Flask.", "source": "model"}],
        llm,
        profile=profile(),
    )

    assert letter.startswith("Dear Acme,")


def test_cover_letter_sees_only_the_bullets_it_was_given():
    """It is composed from the VERIFIED bullets, so it cannot cite a claim the
    verifier already stripped from the resume."""
    llm = FakeProvider([{"cover_letter": "..."}])

    compose_cover_letter(
        job(),
        [{"atom_id": 1, "text": "Cached responses in Redis.", "source": "model"}],
        llm,
        profile=profile(),
    )

    assert "Cached responses in Redis." in llm.prompts[0]
    assert "Flask" not in llm.prompts[0]


def test_a_cover_letter_with_no_bullets_raises_llm_error():
    with pytest.raises(LLMError):
        compose_cover_letter(job(), [], FakeProvider([{"cover_letter": "x"}]), profile=profile())
