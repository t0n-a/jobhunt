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
You are extracting structured education evidence atoms from coursework or transcript text.

Institution: {institution}

Rules:
- Do not invent anything beyond what appears in the text below.
- Group trivially related courses when appropriate.
- Skip generic non-technical courses (e.g. Ethics, English) that carry no technical signal.
- description: one factual sentence of what the course covered.
- skills: a conservative list of technical competencies genuinely evidenced (a course is weaker evidence than a project).

Coursework / Transcript:
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
