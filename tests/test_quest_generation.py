import pytest

from jobhunt.coach.quests import generate_quest
from jobhunt.growth.gaps import Gap
from jobhunt.llm import FakeProvider, LLMError


def gap() -> Gap:
    return Gap(
        skill="Kubernetes",
        job_count=14,
        weight=9.6,
        example_titles=["Platform Engineer", "Backend Engineer"],
    )


def quest_payload(**over) -> dict:
    payload = {
        "title": "Deploy a Python service to k3s",
        "deliverable": "A public GitHub repo with working manifests and a README",
        "target_bullet": (
            "Deployed a Python service to a k3s cluster with declarative manifests, "
            "a readiness probe and a documented rollback."
        ),
        "steps": [
            {
                "title": "Stand up a k3s cluster locally",
                "instructions": "Install k3s and confirm kubectl reaches it.",
                "verification": {"type": "manual"},
            },
            {
                "title": "Write the deployment manifest",
                "instructions": "Add deployment.yaml with a readiness probe.",
                "verification": {
                    "type": "github_commit",
                    "repo": "ada/k8s-lab",
                    "path_glob": "**/*.yaml",
                },
            },
        ],
    }
    payload.update(over)
    return payload


def test_generate_quest_builds_a_quest_and_its_steps():
    llm = FakeProvider([quest_payload()])

    quest, steps = generate_quest(gap(), llm)

    assert quest.skill == "Kubernetes"
    assert quest.title == "Deploy a Python service to k3s"
    assert len(steps) == 2
    assert [s.ordinal for s in steps] == [1, 2]


def test_the_target_bullet_is_present_before_any_work_starts():
    """The reward is shown with the task. Generating it upfront is the mechanic,
    not a nicety — it is what makes the task feel worth starting."""
    llm = FakeProvider([quest_payload()])

    quest, _ = generate_quest(gap(), llm)

    assert "Deployed a Python service" in quest.target_bullet


def test_the_motivating_number_is_stored_on_the_quest():
    """today()["why"] must be able to say "missing from 14 of your best-fit
    jobs" without recomputing gaps."""
    llm = FakeProvider([quest_payload()])

    quest, _ = generate_quest(gap(), llm)

    assert quest.gap_job_count == 14


def test_prompt_carries_the_motivating_number():
    """With ADHD an unexplained task reads as arbitrary and gets skipped."""
    llm = FakeProvider([quest_payload()])

    generate_quest(gap(), llm)

    assert "14" in llm.prompts[0]
    assert "Platform Engineer" in llm.prompts[0]


def test_prompt_asks_for_machine_checkable_steps():
    llm = FakeProvider([quest_payload()])

    generate_quest(gap(), llm)

    assert "verification" in llm.prompts[0].casefold()


def test_verification_specs_are_preserved():
    llm = FakeProvider([quest_payload()])

    _, steps = generate_quest(gap(), llm)

    assert steps[1].verification["repo"] == "ada/k8s-lab"


def test_an_unknown_verification_type_falls_back_to_manual():
    """A model inventing a verification type must not produce a step that can
    never be completed."""
    payload = quest_payload()
    payload["steps"][1]["verification"] = {"type": "telepathy"}
    llm = FakeProvider([payload])

    _, steps = generate_quest(gap(), llm)

    assert steps[1].verification["type"] == "manual"


def test_a_quest_with_no_steps_is_refused():
    llm = FakeProvider([quest_payload(steps=[])])

    with pytest.raises(LLMError):
        generate_quest(gap(), llm)


def test_non_dict_response_raises_llm_error():
    with pytest.raises(LLMError):
        generate_quest(gap(), FakeProvider(["nope"]))


def test_github_user_is_offered_to_the_model_when_known():
    llm = FakeProvider([quest_payload()])

    generate_quest(gap(), llm, github_user="ada")

    assert "ada" in llm.prompts[0]
