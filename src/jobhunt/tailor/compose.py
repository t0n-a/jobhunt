from jobhunt.jobs.models import Job
from jobhunt.llm import LLMError, LLMProvider
from jobhunt.profile.models import EvidenceAtom

BULLET_SCHEMA = {
    "type": "object",
    "properties": {"atom_id": {"type": "integer"}, "text": {"type": "string"}},
    "required": ["atom_id", "text"],
}

RESUME_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "entries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "atom_id": {"type": "integer"},
                                "title": {"type": "string"},
                                "org": {"type": "string"},
                                "dates": {"type": "string"},
                                "bullets": {"type": "array", "items": BULLET_SCHEMA},
                            },
                            "required": ["atom_id", "title", "bullets"],
                        },
                    },
                },
                "required": ["name", "entries"],
            },
        },
        "skills": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "sections", "skills"],
}

COVER_LETTER_SCHEMA = {
    "type": "object",
    "properties": {"cover_letter": {"type": "string"}},
    "required": ["cover_letter"],
}

RESUME_PROMPT = """\
Write a tailored, one-page resume for this candidate and this posting.

You may ONLY use the evidence listed below. Do not invent employers, dates,
metrics, technologies or achievements. If the evidence does not support a claim,
leave the claim out. The candidate has to defend every line of this in an
interview, so an invented detail is a defect, not a flourish.

Rules:
- Every bullet MUST carry the "atom_id" of the evidence item it came from. A
  bullet whose source cannot be traced is discarded.
- Rephrase in the posting's own vocabulary where the evidence honestly supports
  it. Rephrasing is allowed; adding is not.
- Never state a number the evidence item does not state.
- Never name a technology the evidence item does not name.
- Group entries into sections named for what they are: Experience, Projects,
  Education. Put the most relevant section first for this posting.
- 3 bullets per entry at most, and around 12 bullets in the whole document. It
  has to fit one page.
- "skills" lists only technologies the evidence actually evidences.
- "headline" is a short role line, not a slogan. "summary" is at most two
  factual sentences; leave it empty rather than padding it.
- No adjectives like "passionate", "robust", "cutting-edge".

Posting keywords to cover where the evidence honestly supports them:
{keywords}

Job posting:
---
Title: {title}
Company: {company}

{description}
---

Candidate: {name}

Evidence (the ONLY facts you may use):
---
{evidence}
---
"""

COVER_LETTER_PROMPT = """\
Write a short cover letter for this posting.

You may ONLY use the resume lines listed below, which have already been checked
against the candidate's evidence. Do not introduce a claim, a number or a
technology that is not in them.

Rules:
- Four short paragraphs at most. No greeting beyond "Dear <company> team," and
  no postal addresses.
- Plain, factual, specific. No "I am passionate about", no "fast-paced
  environment", no restating the whole resume.
- Say what the candidate built and why it is relevant to this posting.

Job posting:
---
Title: {title}
Company: {company}

{description}
---

Candidate: {name}

Verified resume lines (the ONLY facts you may use):
---
{bullets}
---
"""


def evidence_block(atoms: list[EvidenceAtom]) -> str:
    """Render the shortlist as the only facts the model may use.

    The id is printed because the model has to hand it back on every bullet:
    that id is what makes the bullet checkable.
    """
    lines = []
    for atom in atoms:
        skills = ", ".join(atom.skills) if atom.skills else "none listed"
        metrics = f" metrics: {atom.metrics}." if atom.metrics else ""
        dates = ""
        if atom.started_on or atom.ended_on:
            dates = f" dates: {atom.started_on or '?'} to {atom.ended_on or 'present'}."
        lines.append(
            f"- id={atom.id} [{atom.kind.value}] {atom.title}: {atom.description} "
            f"(skills: {skills}).{metrics}{dates}"
        )
    return "\n".join(lines)


def compose_resume(
    job: Job,
    shortlist: list[EvidenceAtom],
    keywords: list[str],
    llm: LLMProvider,
    *,
    profile,
) -> dict:
    """Select from the shortlist and rewrite it into a resume document.

    Bullets that arrive without a traceable atom_id are dropped here: an
    unattributable claim cannot be verified against anything, so letting it
    through would put an unchecked line in front of an employer.
    """
    response = llm.complete(
        RESUME_PROMPT.format(
            keywords=", ".join(keywords) or "none extracted",
            title=job.title,
            company=job.company,
            description=job.description,
            name=profile.name,
            evidence=evidence_block(shortlist),
        ),
        schema=RESUME_SCHEMA,
    )
    if not isinstance(response, dict):
        raise LLMError("Resume composition expected a JSON object")

    document = {
        "headline": str(response.get("headline", "")),
        "summary": str(response.get("summary", "")),
        "sections": _clean_sections(response.get("sections")),
        "skills": [s for s in response.get("skills", []) if isinstance(s, str) and s.strip()],
    }
    if not any(
        entry["bullets"] for section in document["sections"] for entry in section["entries"]
    ):
        raise LLMError("Resume composition produced no usable bullets")
    return document


def compose_cover_letter(job: Job, bullets: list[dict], llm: LLMProvider, *, profile) -> str:
    """Write the letter from the bullets that PASSED verification.

    Called after the verifier, never alongside it: a letter written from the
    unverified draft could cite a claim the verifier went on to strip.
    """
    if not bullets:
        raise LLMError("Cannot write a cover letter with no verified resume lines")
    response = llm.complete(
        COVER_LETTER_PROMPT.format(
            title=job.title,
            company=job.company,
            description=job.description,
            name=profile.name,
            bullets="\n".join(f"- {b.get('text', '')}" for b in bullets),
        ),
        schema=COVER_LETTER_SCHEMA,
    )
    if not isinstance(response, dict):
        raise LLMError("Cover letter composition expected a JSON object")
    letter = response.get("cover_letter")
    if not isinstance(letter, str) or not letter.strip():
        raise LLMError("Cover letter composition returned nothing")
    return letter


def _clean_sections(raw) -> list[dict]:
    sections = []
    for section in raw or []:
        if not isinstance(section, dict):
            continue
        entries = []
        for entry in section.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            entries.append(
                {
                    "atom_id": entry.get("atom_id"),
                    "title": str(entry.get("title", "")),
                    "org": str(entry.get("org", "")),
                    "dates": str(entry.get("dates", "")),
                    "bullets": _clean_bullets(entry.get("bullets")),
                }
            )
        if entries:
            sections.append({"name": str(section.get("name", "")), "entries": entries})
    return sections


def _clean_bullets(raw) -> list[dict]:
    bullets = []
    for bullet in raw or []:
        if not isinstance(bullet, dict):
            continue
        atom_id = bullet.get("atom_id")
        text = bullet.get("text")
        # No id, no bullet: an unattributable claim cannot be checked, and an
        # unchecked claim is exactly what this phase exists to prevent.
        if not isinstance(atom_id, int) or not isinstance(text, str) or not text.strip():
            continue
        bullets.append({"atom_id": atom_id, "text": text.strip(), "source": "model"})
    return bullets
