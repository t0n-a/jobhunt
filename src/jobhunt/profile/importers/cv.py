import subprocess
from pathlib import Path

from jobhunt.llm import LLMError, LLMProvider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance

CV_ATOM_SCHEMA = {
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

PROMPT = """\
You are extracting structured evidence from a CV.

Break the CV into "evidence atoms" — discrete, bullet-worthy facts. One atom per
project, role, course, or distinct accomplishment.

Rules:
- Do not invent anything. Every atom must be traceable to text in the CV below.
  If a detail is not stated, leave it out rather than guessing.
- Keep descriptions factual and concrete. No marketing language.
- "skills" lists technologies and competencies explicitly evidenced by that atom.
- "metrics" holds any number the CV states for that atom (users, requests,
  percentages, team size). Use null when the CV states none.

CV:
---
{cv_text}
---
"""


def extract_text(path: Path) -> str:
    """Extract plain text from a .pdf, .docx, or .txt file."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".txt":
        return path.read_text(encoding="utf-8")

    if suffix == ".pdf":
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError(f"pdftotext failed on {path.name}: {result.stderr.strip()}")
        return result.stdout

    if suffix == ".docx":
        import docx

        document = docx.Document(str(path))
        return "\n".join(p.text for p in document.paragraphs)

    raise ValueError(f"Unsupported CV format: {suffix}. Use .pdf, .docx, or .txt.")


def import_cv(path: Path, llm: LLMProvider) -> list[EvidenceAtom]:
    """Parse a CV into unconfirmed evidence atoms."""
    path = Path(path)
    text = extract_text(path).strip()
    if not text:
        return []

    response = llm.complete(PROMPT.format(cv_text=text), schema=CV_ATOM_SCHEMA)
    if not isinstance(response, dict):
        raise LLMError("CV extraction expected a JSON object")

    return [
        EvidenceAtom(
            kind=_coerce_kind(item.get("kind")),
            title=item["title"],
            description=item["description"],
            skills=list(item.get("skills") or []),
            metrics=item.get("metrics"),
            provenance=Provenance.CV,
            source_ref=path.name,
            confirmed=False,
        )
        for item in response.get("atoms", [])
    ]


def _coerce_kind(value: str | None) -> AtomKind:
    try:
        return AtomKind(value)
    except ValueError:
        return AtomKind.PROJECT
