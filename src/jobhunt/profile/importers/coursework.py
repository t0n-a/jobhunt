from jobhunt.llm import LLMProvider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance

COURSEWORK_SCHEMA = {
    "type": "object",
    "properties": {
        "atoms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "skills": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "description", "skills"],
            },
        }
    },
    "required": ["atoms"],
}

PROMPT = """\
Convert this coursework listing from {institution} into CV evidence atoms.

Rules:
- Do not invent anything. Only list courses that appear in the text below.
- Group trivially related courses; skip generic ones (e.g. "Ethics", "English")
  that carry no technical signal.
- "description" states what the course covered, in one factual sentence.
- "skills" lists the technical competencies the course genuinely evidences.
  Be conservative: a course is weaker evidence than a project.

Coursework:
---
{text}
---
"""


def import_coursework(text: str, institution: str, llm: LLMProvider) -> list[EvidenceAtom]:
    """Turn pasted coursework or transcript text into unconfirmed EDUCATION evidence atoms."""
    text = text.strip()
    if not text:
        return []

    response = llm.complete(
        PROMPT.format(institution=institution, text=text),
        schema=COURSEWORK_SCHEMA,
    )
    if not isinstance(response, dict):
        return []

    atoms: list[EvidenceAtom] = []
    for item in response.get("atoms", []):
        try:
            atoms.append(
                EvidenceAtom(
                    kind=AtomKind.EDUCATION,
                    title=item["title"],
                    description=item["description"],
                    skills=list(item.get("skills") or []),
                    provenance=Provenance.COURSEWORK,
                    source_ref=institution,
                    confirmed=False,
                )
            )
        except (KeyError, TypeError):
            continue

    return atoms
