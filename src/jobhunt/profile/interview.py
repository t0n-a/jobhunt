from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from jobhunt.llm import LLMError, LLMProvider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance

MAX_TURNS = 8

QUESTION_PROMPT = """\
You are interviewing a recent software engineering graduate to mine concrete,
CV-worthy detail about their work. Their CV is thin, so your job is to surface
specifics they have not written down.

Topic: {topic}

Ask ONE question. Good questions dig for specifics: what was hard, what they
chose and why, what broke, scale or numbers, what they would change. Avoid
yes/no questions and avoid asking two things at once. Do not compliment them.
Return only the question text.

{history}
"""

HARVEST_PROMPT = """\
Convert this interview transcript into CV evidence atoms.

Rules:
- Do not invent anything. Every atom must be traceable to an answer below.
- Use only specifics the candidate actually stated. If a detail is not stated, leave it out rather than guessing.
- skills lists only technical competencies the candidate explicitly evidenced in an answer.
  If a skill is not stated, leave it out rather than inferring it from context.
- "metrics" is null unless the candidate stated a real number.

Transcript:
---
{transcript}
---
"""

ATOM_SCHEMA = {
    "type": "object",
    "properties": {
        "atoms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["project", "experience", "education", "course", "skill"],
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "skills": {"type": "array", "items": {"type": "string"}},
                    "metrics": {"type": ["string", "null"]},
                },
                "required": ["kind", "title", "description", "skills"],
            },
        }
    },
    "required": ["atoms"],
}


def _now() -> datetime:
    return datetime.now(UTC)


class InterviewSession(SQLModel, table=True):
    """One topic-focused interview. `turns` is the full question/answer log."""

    __tablename__ = "interview_session"

    id: int | None = Field(default=None, primary_key=True)
    topic: str
    # Column is not mutation-tracked; any change must assign a new list rather
    # than mutate in place, as in-place mutation silently fails to persist.
    turns: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = "open"
    created_at: datetime = Field(default_factory=_now)


def next_question(session: InterviewSession, llm: LLMProvider) -> str | None:
    """Generate the next question, or None when the interview is complete."""
    if session.turns and session.turns[-1]["answer"] is None:
        raise ValueError("Cannot ask a new question while one is unanswered")

    if len(session.turns) >= MAX_TURNS:
        return None

    history = _format_history(session.turns)
    question = llm.complete(QUESTION_PROMPT.format(topic=session.topic, history=history))
    question = str(question).strip()

    session.turns = [*session.turns, {"question": question, "answer": None}]
    return question


def record_answer(session: InterviewSession, answer: str) -> None:
    """Attach an answer to the open question."""
    if not session.turns or session.turns[-1]["answer"] is not None:
        raise ValueError("There is no open question to answer")

    turns = [dict(t) for t in session.turns]
    turns[-1]["answer"] = answer
    session.turns = turns


def harvest(session: InterviewSession, llm: LLMProvider) -> list[EvidenceAtom]:
    """Turn the answered transcript into unconfirmed evidence atoms.

    Harvesting an already-harvested session is a deliberate no-op returning
    no atoms, making the operation idempotent.
    """
    if session.status == "harvested":
        return []

    answered = [t for t in session.turns if t["answer"]]
    if not answered:
        return []

    body = "\n".join(f"Q: {t['question']}\nA: {t['answer']}" for t in answered)
    if len(answered) < MAX_TURNS:
        transcript = (
            f"Partial interview: {len(answered)} of {MAX_TURNS} questions answered.\n{body}"
        )
    else:
        transcript = body

    response = llm.complete(HARVEST_PROMPT.format(transcript=transcript), schema=ATOM_SCHEMA)
    if not isinstance(response, dict):
        raise LLMError("Interview harvest expected a JSON object")

    session.status = "harvested"

    atoms: list[EvidenceAtom] = []
    for item in response.get("atoms", []):
        try:
            atoms.append(
                EvidenceAtom(
                    kind=_coerce_kind(item.get("kind")),
                    title=item["title"],
                    description=item["description"],
                    skills=list(item.get("skills") or []),
                    metrics=item.get("metrics"),
                    provenance=Provenance.INTERVIEW,
                    source_ref=session.topic,
                    confirmed=False,
                )
            )
        except (KeyError, TypeError):
            continue
    return atoms


def _format_history(turns: list[dict]) -> str:
    if not turns:
        return "This is the first question."
    lines = ["Interview so far:"]
    for turn in turns:
        lines.append(f"Q: {turn['question']}")
        lines.append(f"A: {turn['answer']}")
    return "\n".join(lines)


def _coerce_kind(value: str | None) -> AtomKind:
    try:
        return AtomKind(value)
    except ValueError:
        return AtomKind.PROJECT
