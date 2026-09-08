import pytest

from jobhunt.jobs.models import Job, posting_hash
from jobhunt.llm import FakeProvider, LLMError
from jobhunt.tailor.keywords import extract_keywords


def job(description="We need Python, Kubernetes and CI/CD experience.") -> Job:
    return Job(
        title="Platform Engineer",
        company="Acme",
        description=description,
        url="u",
        content_hash=posting_hash("Platform Engineer", "Acme", description),
    )


def test_keywords_are_extracted_from_the_posting():
    llm = FakeProvider([{"keywords": ["Python", "Kubernetes", "CI/CD"]}])

    assert extract_keywords(job(), llm) == ["Python", "Kubernetes", "CI/CD"]


def test_keywords_are_canonicalised():
    """Coverage is measured against these. Left alone, "Amazon Web Services"
    would be reported missing from a resume that says "AWS"."""
    llm = FakeProvider([{"keywords": ["Amazon Web Services", "k8s"]}])

    assert extract_keywords(job(), llm) == ["AWS", "Kubernetes"]


def test_duplicate_spellings_collapse_to_one_keyword():
    llm = FakeProvider([{"keywords": ["AWS", "Amazon Web Services", "aws"]}])

    assert extract_keywords(job(), llm) == ["AWS"]


def test_the_posting_text_reaches_the_prompt():
    llm = FakeProvider([{"keywords": ["Python"]}])

    extract_keywords(job("We need Terraform on GCP."), llm)

    assert "Terraform on GCP" in llm.prompts[0]
    assert "Platform Engineer" in llm.prompts[0]


def test_the_limit_caps_what_is_returned():
    """A percentage over eighty keywords, half of them noise, makes a good
    resume look bad and pushes the user to stuff their bullets."""
    llm = FakeProvider([{"keywords": [f"Skill{i}" for i in range(50)]}])

    assert len(extract_keywords(job(), llm, limit=10)) == 10


def test_a_non_dict_response_raises_llm_error():
    with pytest.raises(LLMError):
        extract_keywords(job(), FakeProvider(["not json"]))


def test_a_response_with_no_keywords_raises_llm_error():
    """Tailoring against an empty keyword set would select atoms at random."""
    with pytest.raises(LLMError):
        extract_keywords(job(), FakeProvider([{"keywords": []}]))
