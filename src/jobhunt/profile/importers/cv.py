import subprocess
from pathlib import Path

import docx
from docx.opc.exceptions import PackageNotFoundError

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
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"pdftotext was not found while processing {path.name}. "
                "poppler-utils is required to parse PDF files."
            ) from exc
        if result.returncode != 0:
            raise ValueError(f"pdftotext failed on {path.name}: {result.stderr.strip()}")
        text = result.stdout
        if not text.strip():
            raise ValueError(
                f"No extractable text found in {path.name}. The file may be a scanned image."
            )
        return text

    if suffix == ".docx":
        try:
            document = docx.Document(str(path))
        except PackageNotFoundError as exc:
            raise ValueError(f"Failed to read docx file {path.name}: {exc}") from exc
        text = "\n".join(p.text for p in document.paragraphs)
        if not text.strip():
            raise ValueError(
                f"No extractable text found in {path.name}. The file may be a scanned image."
            )
        return text

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
                    provenance=Provenance.CV,
                    source_ref=path.name,
                    confirmed=False,
                )
            )
        except (KeyError, TypeError):
            continue
    return atoms


def _coerce_kind(value: str | None) -> AtomKind:
    try:
        return AtomKind(value)
    except ValueError:
        return AtomKind.PROJECT
