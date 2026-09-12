from jobhunt.jobs.models import Job, Score
from jobhunt.llm import LLMError, LLMProvider
from jobhunt.matching.skills import canonical_skills
from jobhunt.profile.models import EvidenceAtom


class EmptyProfileError(LLMError):
    """Raised when scoring is attempted with no confirmed evidence atoms."""


TRACKS = ("backend", "fullstack", "frontend", "data", "devops", "ai", "other")
SENIORITY = ("under", "match", "over")

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "matched_skills": {"type": "array", "items": {"type": "string"}},
        "missing_skills": {"type": "array", "items": {"type": "string"}},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "seniority_fit": {"type": "string", "enum": list(SENIORITY)},
        "track": {"type": "string", "enum": list(TRACKS)},
        "rationale": {"type": "string"},
    },
    "required": [
        "fit_score",
        "matched_skills",
        "missing_skills",
        "red_flags",
        "seniority_fit",
        "track",
        "rationale",
    ],
}

PROMPT = """\
Score this job posting against the candidate's profile.

Rules:
- "fit_score" is 0-100 for how well the candidate's EVIDENCED experience matches
  what the posting asks for. Judge only what the profile below actually states.
- "matched_skills" are skills the posting wants AND the profile evidences.
- "missing_skills" are skills the posting wants that the profile does NOT
  evidence. Be precise and specific: name the technology, not a category, and do
  not invent a requirement the posting never stated. This field decides what the
  candidate is told to learn next, so a vague or invented entry sends them to
  study the wrong thing.
- "seniority_fit" is "under", "match" or "over" for the candidate against the
  posting's stated experience level. The candidate is a recent graduate, so
  "under" is expected and normal. Flag it, but DO NOT REDUCE the fit score for
  it — stated year requirements are frequently soft and the candidate decides
  what to reach for.
- Treat an on-site role in Porto, Portugal and a fully remote role EQUALLY. Do
  not raise or lower the score for either.
- "track" classifies the role as one of: backend, fullstack, frontend, data,
  devops, ai, other.
- "red_flags" are concrete concerns stated in the posting (unpaid, heavy
  on-call, a required security clearance, an expired listing). Leave empty when
  there are none; do not invent concerns.
- "rationale" is one or two factual sentences. No encouragement, no filler.
- Lower fit_score for a job that violates a stated constraint. Do not raise fit_score for a goal — goals decide track, not fit.

Job posting:
---
{posting}
---

Candidate profile (confirmed evidence only):
---
{profile}
---
"""


def profile_summary(atoms: list[EvidenceAtom]) -> str:
    """Render confirmed atoms into the block shown to the scorer."""
    if not atoms:
        return "This candidate has no confirmed evidence atoms yet."
    lines = []
    for atom in atoms:
        skills = ", ".join(atom.skills) if atom.skills else "none listed"
        lines.append(f"- [{atom.kind.value}] {atom.title}: {atom.description} (skills: {skills})")
    return "\n".join(lines)


def job_summary(job: Job) -> str:
    """Render a job into the block shown to the scorer."""
    return (
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location or 'unspecified'}\n"
        f"Remote: {job.remote}\n\n"
        f"{job.description}"
    )


def score_job(job: Job, atoms: list[EvidenceAtom], llm: LLMProvider, *, notes: str = "") -> Score:
    """Score one job against the confirmed profile.

    Only confirmed atoms should reach this function: scoring the candidate
    against unreviewed model output would rank them on claims they never checked.

    Refuses an empty profile. A live scan against one produced a fit_score of 100
    for a face-photo collection gig: with nothing to match, the model rates the
    jobs demanding least. A confident wrong ranking is worse than no ranking.
    """
    if not atoms:
        raise EmptyProfileError(
            "Cannot score against an empty profile. Confirm at least one evidence "
            "atom first — run the interview or import a CV, then confirm the atoms."
        )

    profile = profile_summary(atoms) + (f"\n\n{notes}" if notes else "")
    response = llm.complete(
        PROMPT.format(posting=job_summary(job), profile=profile),
        schema=SCORE_SCHEMA,
    )
    if not isinstance(response, dict):
        raise LLMError("Scoring expected a JSON object")

    try:
        fit = int(response["fit_score"])
        rationale = response["rationale"]
        seniority = response["seniority_fit"]
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError(f"Scoring response missing required fields: {exc}") from exc

    track = response.get("track")
    return Score(
        job_id=job.id,
        fit_score=max(0, min(100, fit)),
        matched_skills=canonical_skills(response.get("matched_skills")),
        missing_skills=canonical_skills(response.get("missing_skills")),
        red_flags=_clean(response.get("red_flags")),
        seniority_fit=seniority if seniority in SENIORITY else "match",
        track=track if track in TRACKS else "other",
        rationale=rationale,
        model=getattr(llm, "model", ""),
        content_hash=job.content_hash,
    )


def _clean(values) -> list[str]:
    """Keep only non-empty strings, de-duplicated, order preserved.

    Models occasionally emit blanks or repeats. `missing_skills` feeds the gap
    ranking that decides what the candidate studies next, so a duplicate there
    would double-weight one skill against all the others.
    """
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        key = stripped.casefold()
        if stripped and key not in seen:
            seen.add(key)
            out.append(stripped)
    return out
