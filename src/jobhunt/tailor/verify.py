import re
from dataclasses import dataclass

from jobhunt.matching.skills import SKILL_ALIASES, canonical_skill, mentions_term
from jobhunt.profile.models import EvidenceAtom
from jobhunt.tailor.models import iter_bullets, keep_bullets

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

# SKILL_ALIASES contains ordinary English words. Matching these case-insensitively
# would read "the rest of the pipeline" as a REST claim and drop a true bullet, so
# for this set the canonical spelling must appear exactly, which is how a resume
# writes a technology anyway.
AMBIGUOUS = {"go", "rest", "node", "react", "js", "r"}


@dataclass
class VerificationResult:
    passed: bool
    reason: str | None = None


def verify_bullet(
    bullet: dict, atom: EvidenceAtom | None, shortlist_ids: set[int]
) -> VerificationResult:
    """Check one bullet against the atom it claims to come from.

    A false positive drops a true bullet, which the user can see in
    dropped_bullets and restore by hand. A false negative puts a fabricated
    claim in front of an interviewer. The checks are biased accordingly.
    """
    # The user's own words. They are responsible for them, and a verifier that
    # fights manual corrections is a verifier the user routes around.
    if bullet.get("source") == "user":
        return VerificationResult(True)

    text = str(bullet.get("text", ""))
    atom_id = bullet.get("atom_id")
    if not isinstance(atom_id, int):
        return VerificationResult(False, "Bullet names no source atom.")
    if atom_id not in shortlist_ids:
        return VerificationResult(False, f"Atom {atom_id} was not in the shortlist.")
    if atom is None:
        return VerificationResult(False, f"Atom {atom_id} no longer exists.")
    if not atom.confirmed:
        return VerificationResult(False, f"Atom {atom_id} is not confirmed.")

    haystack = " ".join(
        part for part in (atom.title, atom.description, atom.metrics or "", " ".join(atom.skills))
    )

    invented_number = _invented_number(text, haystack)
    if invented_number is not None:
        return VerificationResult(False, f"States {invented_number}, which the evidence does not.")

    invented_tech = _invented_technology(text, atom, haystack)
    if invented_tech is not None:
        return VerificationResult(False, f"Names {invented_tech}, which the evidence does not.")

    return VerificationResult(True)


def verify_document(
    resume_json: dict, atoms: dict[int, EvidenceAtom], shortlist_ids: set[int]
) -> tuple[dict, list[dict]]:
    """Strip every bullet that fails verification.

    Returns a NEW document holding the survivors, and the rejects as
    {atom_id, text, reason} so the UI can show what the model tried to claim.
    """
    kept: set[int] = set()
    dropped: list[dict] = []
    for index, bullet in iter_bullets(resume_json):
        atom = atoms.get(bullet.get("atom_id"))
        result = verify_bullet(bullet, atom, shortlist_ids)
        if result.passed:
            kept.add(index)
        else:
            dropped.append(
                {
                    "atom_id": bullet.get("atom_id"),
                    "text": bullet.get("text", ""),
                    "reason": result.reason or "Rejected.",
                }
            )
    return keep_bullets(resume_json, kept), dropped


def _invented_number(text: str, haystack: str) -> str | None:
    """Return the first number in the bullet that the evidence never states."""
    for match in NUMBER.finditer(text):
        number = match.group(0)
        if number not in haystack:
            return number
    return None


def _invented_technology(text: str, atom: EvidenceAtom, haystack: str) -> str | None:
    """Return the first technology the bullet claims and the atom does not.

    Only technologies in the curated SKILL_ALIASES map are checked. The map is
    the same allowlist that keeps gap ranking honest; extending it extends this
    check, and guessing at arbitrary capitalised words would drop true bullets
    for naming a company.
    """
    claimed = {canonical_skill(skill).casefold() for skill in atom.skills}
    evidence = haystack.casefold()

    for alias, canonical in SKILL_ALIASES.items():
        if alias in AMBIGUOUS:
            if not mentions_term(text, canonical, case_sensitive=True):
                continue
        elif not mentions_term(text, alias):
            continue

        if canonical.casefold() in claimed:
            continue
        if mentions_term(evidence, alias) or mentions_term(evidence, canonical):
            continue
        return canonical
    return None
