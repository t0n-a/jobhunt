import pytest

from jobhunt.jobs.models import Job
from jobhunt.llm import FakeProvider, LLMError
from jobhunt.matching.score import (
    TRACKS,
    EmptyProfileError,
    profile_summary,
    score_job,
)
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance


def atom(title="URL shortener", skills=("Python", "Redis")) -> EvidenceAtom:
    return EvidenceAtom(
        kind=AtomKind.PROJECT,
        title=title,
        description="Flask service with Redis caching.",
        skills=list(skills),
        provenance=Provenance.INTERVIEW,
        confirmed=True,
    )


def job(title="Backend Engineer", description="Python, Flask and Redis. 2+ years.") -> Job:
    return Job(
        id=1,
        title=title,
        company="Acme",
        description=description,
        url="https://example.com/1",
        content_hash="hash-1",
    )


def good_response(**over) -> dict:
    payload = {
        "fit_score": 78,
        "matched_skills": ["Python", "Redis"],
        "missing_skills": ["Kubernetes"],
        "red_flags": [],
        "seniority_fit": "under",
        "track": "backend",
        "rationale": "Strong Python and Redis overlap; lacks Kubernetes.",
    }
    payload.update(over)
    return payload


def test_profile_summary_includes_titles_and_skills():
    summary = profile_summary([atom()])

    assert "URL shortener" in summary
    assert "Python" in summary


def test_profile_summary_of_an_empty_profile_is_explicit():
    assert "no confirmed" in profile_summary([]).casefold()


def test_score_job_maps_all_fields():
    llm = FakeProvider([good_response()])

    score = score_job(job(), [atom()], llm)

    assert score.fit_score == 78
    assert score.matched_skills == ["Python", "Redis"]
    assert score.missing_skills == ["Kubernetes"]
    assert score.seniority_fit == "under"
    assert score.track == "backend"
    assert score.job_id == 1
    assert score.content_hash == "hash-1"


def test_prompt_contains_the_posting_and_the_profile():
    llm = FakeProvider([good_response()])

    score_job(job(), [atom()], llm)

    prompt = llm.prompts[0]
    assert "Backend Engineer" in prompt
    assert "URL shortener" in prompt


def test_prompt_weighs_porto_and_remote_equally():
    llm = FakeProvider([good_response()])

    score_job(job(), [atom()], llm)

    assert "equally" in llm.prompts[0].casefold()


def test_prompt_instructs_that_stretch_roles_are_scored_not_penalised():
    llm = FakeProvider([good_response()])

    score_job(job(), [atom()], llm)

    assert "do not reduce" in llm.prompts[0].casefold()


def test_prompt_forbids_inventing_missing_skills():
    """missing_skills decides what the candidate studies next, so an invented
    requirement sends them to learn the wrong thing."""
    llm = FakeProvider([good_response()])

    score_job(job(), [atom()], llm)

    # Asserted on a fragment that cannot span the prompt's line wrapping.
    assert "not invent a requirement" in llm.prompts[0].casefold()


def test_fit_score_is_clamped_into_range():
    assert score_job(job(), [atom()], FakeProvider([good_response(fit_score=250)])).fit_score == 100
    assert score_job(job(), [atom()], FakeProvider([good_response(fit_score=-5)])).fit_score == 0


def test_unknown_track_falls_back_to_other():
    llm = FakeProvider([good_response(track="astrology")])

    assert score_job(job(), [atom()], llm).track == "other"


def test_known_tracks_survive():
    for track in TRACKS:
        llm = FakeProvider([good_response(track=track)])
        assert score_job(job(), [atom()], llm).track == track


def test_unknown_seniority_falls_back_to_match():
    llm = FakeProvider([good_response(seniority_fit="wildly senior")])

    assert score_job(job(), [atom()], llm).seniority_fit == "match"


def test_skill_lists_are_deduplicated_and_stripped():
    """A duplicate in missing_skills would double-weight that skill in the gap
    ranking that decides what the candidate learns next."""
    llm = FakeProvider([good_response(missing_skills=["Kubernetes", " kubernetes ", "", "Docker"])])

    assert score_job(job(), [atom()], llm).missing_skills == ["Kubernetes", "Docker"]


def test_non_string_entries_in_skill_lists_are_dropped():
    llm = FakeProvider([good_response(matched_skills=["Python", None, 42, "Redis"])])

    assert score_job(job(), [atom()], llm).matched_skills == ["Python", "Redis"]


def test_non_dict_response_raises_llm_error():
    with pytest.raises(LLMError):
        score_job(job(), [atom()], FakeProvider(["not a dict"]))


def test_missing_required_field_raises_llm_error():
    with pytest.raises(LLMError):
        score_job(job(), [atom()], FakeProvider([{"matched_skills": []}]))


def test_scoring_an_empty_profile_is_refused():
    """A live scan against an empty profile gave a face-photo collection gig a
    fit_score of 100: with nothing to match, the model rates whichever job
    demands least. A confident wrong ranking is worse than no ranking."""
    llm = FakeProvider([good_response()])

    with pytest.raises(EmptyProfileError) as excinfo:
        score_job(job(), [], llm)

    assert "confirm" in str(excinfo.value).casefold()
    assert llm.prompts == []  # refused before spending an LLM call


GOLDEN = [
    # (job title, description, fake fit_score) — ordered best to worst
    ("Junior Backend Engineer", "Python, Flask, Redis. Graduates welcome.", 88),
    ("Backend Engineer", "Python and PostgreSQL. 2+ years.", 71),
    ("Senior Kubernetes Platform Engineer", "K8s, Terraform, Go. 6+ years.", 24),
]


def test_golden_set_preserves_relative_ordering():
    """Spec §10: assert relative ordering, not exact scores. Exact LLM scores are
    not stable enough to pin; this proves a higher score survives intact through
    score_job into Score."""
    results = []
    for title, description, fake_fit in GOLDEN:
        llm = FakeProvider([good_response(fit_score=fake_fit)])
        results.append(score_job(job(title, description), [atom()], llm).fit_score)

    assert results == sorted(results, reverse=True), (
        f"scores must preserve the golden ordering, got {results}"
    )


def test_golden_set_entries_all_carry_a_track_and_rationale():
    for title, description, fake_fit in GOLDEN:
        llm = FakeProvider([good_response(fit_score=fake_fit)])
        score = score_job(job(title, description), [atom()], llm)

        assert score.track in TRACKS
        assert score.rationale


def test_skill_aliases_are_canonicalised():
    """A live scan produced "Amazon Web Services" and "AWS" as separate gaps,
    splitting the top skill across two entries and under-ranking it."""
    llm = FakeProvider(
        [good_response(missing_skills=["Amazon Web Services", "AWS", "k8s", "Kubernetes"])]
    )

    assert score_job(job(), [atom()], llm).missing_skills == ["AWS", "Kubernetes"]


def test_unknown_skills_pass_through_unchanged():
    """The alias map is a curated allowlist, not a guesser — a wrong merge would
    send the user to study the wrong thing."""
    llm = FakeProvider([good_response(missing_skills=["Elixir", "Zig"])])

    assert score_job(job(), [atom()], llm).missing_skills == ["Elixir", "Zig"]
