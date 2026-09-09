"""One model call producing every LinkedIn field.

One call rather than one per field: separate calls cannot see each other, so a
headline and an About section written independently both reach for the same
strongest achievement and the profile repeats itself. One call is what lets the
model spend the best evidence once.
"""

from jobhunt.config import ProfileConfig
from jobhunt.linkedin.fields import field_spec
from jobhunt.llm import LLMError, LLMProvider
from jobhunt.profile.models import EvidenceAtom
from jobhunt.tailor.compose import as_atom_id, evidence_block

ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "atom_id": {"type": "integer"},
        "title": {"type": "string"},
        "text": {"type": "string"},
    },
    "required": ["atom_id", "title", "text"],
}

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "about": {"type": "string"},
        "experience": {"type": "array", "items": ENTRY_SCHEMA},
        "projects": {"type": "array", "items": ENTRY_SCHEMA},
        "skills": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "about", "experience", "projects", "skills"],
}

PROFILE_PROMPT = """\
Write the text of a LinkedIn profile for this candidate.

You may ONLY use the evidence listed below. Do not invent employers, dates,
metrics, technologies or achievements. If the evidence does not support a claim,
leave the claim out. The candidate has to defend every line of this in an
interview, so an invented detail is a defect, not a flourish.

Rules:
- Every entry under "experience" and "projects" MUST carry the "atom_id" of the
  evidence item it came from. An entry whose source cannot be traced is discarded.
- Never state a number the evidence does not state.
- Never name a technology the evidence does not name.
- "headline" is a role line under {headline_limit} characters. Not a slogan.
- "about" is first-person prose under {about_limit} characters, three short
  paragraphs at most. It may draw on several evidence items in one sentence.
  Plain and specific. No "passionate", no "results-driven", no "proven track
  record".
- Each "experience" entry is one description under {experience_limit} characters:
  what was built, with what, and what came of it.
- Each "projects" entry is one description under {projects_limit} characters:
  what was built, with what, and what came of it.
- "skills" lists only technologies the evidence actually evidences, at most
  {skills_limit} of them, strongest first.

Roles this candidate is aiming at, to shape emphasis where the evidence
honestly supports it:
{keywords}

Candidate: {name}
Location: {location}

Evidence (the ONLY facts you may use):
---
{evidence}
---
"""


def compose_profile(
    shortlist: list[EvidenceAtom],
    keywords: list[str],
    llm: LLMProvider,
    *,
    profile: ProfileConfig,
) -> dict:
    """Draft every field from the shortlist.

    Entries arriving without a traceable atom_id are dropped here: an
    unattributable claim cannot be verified against anything, so letting it
    through would put an unchecked line on a public profile.

    Nothing this returns is verified yet. Task 8's build step is what checks it,
    and nothing reaches the database before then.
    """
    response = llm.complete(
        PROFILE_PROMPT.format(
            headline_limit=field_spec("headline").limit,
            about_limit=field_spec("about").limit,
            experience_limit=field_spec("experience").limit,
            projects_limit=field_spec("projects").limit,
            skills_limit=field_spec("skills").limit,
            keywords=", ".join(keywords) or "none yet — write for the evidence itself",
            name=profile.name,
            location=profile.location,
            evidence=evidence_block(shortlist),
        ),
        schema=PROFILE_SCHEMA,
    )
    if not isinstance(response, dict):
        raise LLMError("Profile composition expected a JSON object")

    document = {
        "headline": str(response.get("headline", "")).strip(),
        "about": str(response.get("about", "")).strip(),
        "experience": _clean_entries(response.get("experience")),
        "projects": _clean_entries(response.get("projects")),
        "skills": [
            s.strip() for s in response.get("skills") or [] if isinstance(s, str) and s.strip()
        ],
    }
    if not any(
        (
            document["headline"],
            document["about"],
            document["experience"],
            document["projects"],
            document["skills"],
        )
    ):
        raise LLMError("Profile composition produced no usable content")
    return document


def _clean_entries(raw) -> list[dict]:
    """Keep the entries that can be checked, in the order given.

    The shape returned — atom_id, text, source — is exactly what
    tailor.verify.verify_bullet takes, so entries go through the strong
    per-atom verifier without translation.
    """
    entries = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        atom_id = as_atom_id(entry.get("atom_id"))
        text = entry.get("text")
        if atom_id is None or not isinstance(text, str) or not text.strip():
            continue
        entries.append(
            {
                "atom_id": atom_id,
                "title": str(entry.get("title", "")).strip(),
                "text": text.strip(),
                "source": "model",
            }
        )
    return entries
