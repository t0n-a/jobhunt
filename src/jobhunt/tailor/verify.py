import re
from dataclasses import dataclass

from jobhunt.matching.skills import SKILL_ALIASES, canonical_skill, mentions_term
from jobhunt.profile.models import EvidenceAtom
from jobhunt.tailor.models import iter_bullets, keep_bullets

# Bounded the same way the evidence side is matched. Without the lookarounds
# this pulls "95" out of "p95" and then demands the evidence carry a bare 95,
# dropping a bullet whose percentile the atom states in full.
NUMBER = re.compile(r"(?<![A-Za-z0-9+#.])\d+(?:[.,]\d+)?(?![A-Za-z0-9+#])")

# SKILL_ALIASES contains ordinary English words. Matching these case-insensitively
# would read "the rest of the pipeline" as a REST claim and drop a true bullet, so
# for this set the canonical spelling must appear exactly — on BOTH sides of the
# check, bullet and evidence alike.
#
# Only genuine English homographs belong here, because an exempt alias is a
# weaker check: "js" is not a homograph and was removed, which is what makes
# "Wrote JS helpers" checkable at all.
AMBIGUOUS = {"go", "rest", "node", "react"}


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
        atom_id = bullet.get("atom_id")
        # A model can emit a list or a dict here, and dict.get would raise on an
        # unhashable key — taking the whole document down over one bad bullet.
        atom = atoms.get(atom_id) if isinstance(atom_id, int) else None
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
    """Return the first number in the bullet the evidence does not carry.

    Matched on word boundaries rather than as a substring. A raw `in` test reads
    "3" inside "1.3 million" and "9" inside "2019", so any atom carrying a year
    would silently authorise a fabricated headcount — the exact failure this
    module exists to prevent, reachable from entirely ordinary evidence.

    Extraction is bounded the same way, so a number embedded in a label is never
    pulled out and demanded on its own: "p95" yields nothing to check, and the
    reported number is the one actually fabricated rather than the first digits
    in the line.
    """
    for match in NUMBER.finditer(text):
        number = match.group(0)
        if not mentions_term(haystack, number):
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

    for alias, canonical in SKILL_ALIASES.items():
        ambiguous = alias in AMBIGUOUS
        if ambiguous:
            if not mentions_term(text, canonical, case_sensitive=True):
                continue
        elif not mentions_term(text, alias):
            continue

        if canonical.casefold() in claimed:
            continue
        # The homograph exemption has to hold on the evidence side too. Matched
        # loosely, an atom reading "cleaned the rest of the data" would stand as
        # evidence for "Designed the REST API", laundering the claim through a
        # word the atom used in its ordinary English sense.
        if ambiguous:
            if mentions_term(haystack, canonical, case_sensitive=True):
                continue
        elif mentions_term(haystack, alias) or mentions_term(haystack, canonical):
            continue
        return canonical
    return None
