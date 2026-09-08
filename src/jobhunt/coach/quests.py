from jobhunt.coach.models import Quest, QuestStep, VerificationType
from jobhunt.growth.gaps import Gap
from jobhunt.llm import LLMError, LLMProvider

QUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "deliverable": {"type": "string"},
        "target_bullet": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "instructions": {"type": "string"},
                    "verification": {"type": "object"},
                },
                "required": ["title", "instructions", "verification"],
            },
        },
    },
    "required": ["title", "deliverable", "target_bullet", "steps"],
}

PROMPT = """\
Design a short project that closes one specific skill gap for a recent software
engineering graduate, and produces something they can put on a CV.

The gap: {skill}
It is missing from {job_count} of their best-fit job matches, including roles
titled: {titles}.

Rules:
- The project must be completable in 3 to 5 short sessions by one person.
- "deliverable" is one concrete artefact, normally a public GitHub repository.
- "target_bullet" is the CV line they will have earned when it is done. Write it
  now, in the past tense, as it would appear on the CV. Be specific and factual —
  they must be able to defend every word of it in an interview. No adjectives
  like "robust" or "cutting-edge".
- "steps" are 3 to 5 day-sized slices, each one sitting.
- Each step needs a "verification" object saying how completion can be CHECKED:
    {{"type": "github_commit", "repo": "<owner/name>", "path_glob": "**/*.yaml"}}
    {{"type": "github_repo_exists", "repo": "<owner/name>"}}
    {{"type": "github_file_contains", "repo": "<owner/name>", "path": "README.md", "text": "..."}}
    {{"type": "manual"}}
  Prefer a checkable type wherever the step produces code or files. Use "manual"
  only for steps that genuinely leave no trace in a repository, such as reading
  documentation or watching a lecture.
- Do not invent skills the gap did not name, and do not pad the project with
  unrelated technologies.
{github_hint}
"""

GITHUB_HINT = "- Their GitHub username is {user}; use it as the repo owner.\n"


def generate_quest(
    gap: Gap, llm: LLMProvider, *, github_user: str | None = None
) -> tuple[Quest, list[QuestStep]]:
    """Turn one ranked gap into a quest with day-sized, checkable steps."""
    prompt = PROMPT.format(
        skill=gap.skill,
        job_count=gap.job_count,
        titles=", ".join(gap.example_titles) or "several roles",
        github_hint=GITHUB_HINT.format(user=github_user) if github_user else "",
    )
    response = llm.complete(prompt, schema=QUEST_SCHEMA)
    if not isinstance(response, dict):
        raise LLMError("Quest generation expected a JSON object")

    raw_steps = response.get("steps") or []
    if not raw_steps:
        raise LLMError("Quest generation returned no steps")

    try:
        quest = Quest(
            skill=gap.skill,
            gap_job_count=gap.job_count,
            title=response["title"],
            deliverable=response["deliverable"],
            target_bullet=response["target_bullet"],
        )
    except (KeyError, TypeError) as exc:
        raise LLMError(f"Quest generation missing required fields: {exc}") from exc

    steps: list[QuestStep] = []
    for ordinal, raw in enumerate(raw_steps, start=1):
        try:
            steps.append(
                QuestStep(
                    quest_id=0,  # assigned when the quest is persisted
                    ordinal=ordinal,
                    title=raw["title"],
                    instructions=raw["instructions"],
                    verification=_coerce_verification(raw.get("verification")),
                )
            )
        except (KeyError, TypeError, AttributeError):
            continue

    if not steps:
        raise LLMError("Quest generation produced no usable steps")
    return quest, steps


def _coerce_verification(spec) -> dict:
    """An unknown or malformed verification type degrades to manual.

    A step whose verification can never run is a step that can never be
    completed, which would strand the whole quest.
    """
    if not isinstance(spec, dict):
        return {"type": VerificationType.MANUAL.value}
    kind = spec.get("type")
    if kind not in {v.value for v in VerificationType}:
        return {"type": VerificationType.MANUAL.value}
    return dict(spec)
