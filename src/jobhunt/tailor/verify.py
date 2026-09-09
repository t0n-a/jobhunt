import re
from dataclasses import dataclass

from jobhunt.matching.skills import SKILL_ALIASES, canonical_skill, mentions_term
from jobhunt.profile.models import EvidenceAtom
from jobhunt.tailor.models import iter_bullets, keep_bullets

# Left-bounded only. The lookbehind stops "95" being pulled out of "p95" and
# then demanded on its own, which would drop a bullet whose percentile the
# atom states in full. There is deliberately NO right boundary: a number
# carrying a unit — "3x throughput", "$50k saved" — is still a number, and
# that is exactly the shape a fabricated resume metric takes.
NUMBER = re.compile(r"(?<![A-Za-z0-9+#.])\d+(?:[.,]\d+)?")

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

    invented_tech = _invented_technology(
        text, {canonical_skill(skill).casefold() for skill in atom.skills}, haystack
    )
    if invented_tech is not None:
        return VerificationResult(False, f"Names {invented_tech}, which the evidence does not.")

    return VerificationResult(True)


def verify_prose(text: str, atoms: list[EvidenceAtom]) -> VerificationResult:
    """Check a synthesised passage against the union of the evidence.

    A LinkedIn headline or About section blends many atoms, so there is no
    single source atom and verify_bullet cannot apply. The guarantee here is
    deliberately weaker, and worth stating plainly: every number and every
    recognised technology in the passage appears SOMEWHERE in the confirmed
    evidence. It does not establish that a given claim came from a given atom.

    That is the honest guarantee for synthesised prose, and it still covers
    where fabrication actually costs an interview — an invented metric or an
    unearned technology.
    """
    if not atoms:
        # With an empty haystack every check below trivially passes, so the
        # answer would be "verified" for text nothing supports.
        return VerificationResult(False, "There is no evidence to check this against.")

    haystack = " ".join(
        part
        for atom in atoms
        for part in (atom.title, atom.description, atom.metrics or "", " ".join(atom.skills))
    )
    claimed = {canonical_skill(skill).casefold() for atom in atoms for skill in atom.skills}

    invented_number = _invented_number(text, haystack)
    if invented_number is not None:
        return VerificationResult(False, f"States {invented_number}, which your evidence does not.")

    invented_tech = _invented_technology(text, claimed, haystack)
    if invented_tech is not None:
        return VerificationResult(False, f"Names {invented_tech}, which your evidence does not.")

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

    Extraction is bounded on the left the same way, so a number embedded in a
    label is never pulled out and demanded on its own: "p95" yields nothing to
    check, and the reported number is the one actually fabricated rather than
    the first digits in the line. It is NOT bounded on the right, because
    "3x throughput" and "$50k saved" are numbers a resume fabricates and a
    right boundary would make them invisible to this check.
    """
    for match in NUMBER.finditer(text):
        number = match.group(0)
        if not _states_number(haystack, number):
            return number
    return None


def _states_number(haystack: str, number: str) -> bool:
    """Does the evidence carry this number, as a number?

    The left boundary is as strict as extraction, so "9" is not found inside
    "2019" and "100" is not found inside "10000". The right side forbids only
    another DIGIT, never a letter, so a unit suffix still counts as evidence:
    an atom that says "3x throughput" does state the 3.
    """
    pattern = rf"(?<![A-Za-z0-9+#.]){re.escape(number)}(?!\d)"
    return re.search(pattern, haystack) is not None


def _invented_technology(text: str, claimed: set[str], haystack: str) -> str | None:
    """Return the first technology the text claims and the evidence does not.

    Takes the set of canonical skills the evidence claims rather than an atom,
    so the same check serves one bullet (that bullet's atom) and a whole
    passage (the union of the shortlist). Only technologies in the curated
    SKILL_ALIASES map are checked. The map is the same allowlist that keeps gap
    ranking honest; extending it extends this check, and guessing at arbitrary
    capitalised words would drop true bullets for naming a company.
    """
    for alias, canonical in SKILL_ALIASES.items():
        ambiguous = alias in AMBIGUOUS
        # An ambiguous alias must appear in its canonical spelling, exactly. Some
        # canonicals carry a suffix a resume routinely drops -- "Node.js" written
        # as "Node" -- so the stem counts too. The stem is derived from the
        # canonical rather than by title-casing the alias, because "Rest" at the
        # start of a sentence is not a REST claim.
        spellings = {canonical, canonical.split(".")[0]}
        if ambiguous:
            if not any(mentions_term(text, s, case_sensitive=True) for s in spellings):
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
            if any(mentions_term(haystack, s, case_sensitive=True) for s in spellings):
                continue
        elif mentions_term(haystack, alias) or mentions_term(haystack, canonical):
            continue
        return canonical
    return None


def verify_skills(skills: list[str], atoms: dict[int, EvidenceAtom]) -> tuple[list[str], list[str]]:
    """Split a skills line into what the evidence supports and what it does not.

    The line is model-written and never passes through verify_bullet, so without
    this it is the one place on the page where an unevidenced technology reaches
    an employer unchallenged, and a skills list is exactly where an interviewer
    picks the thing to ask about.
    """
    kept: list[str] = []
    unevidenced: list[str] = []
    for skill in skills:
        if any(_atom_supports(atom, canonical_skill(skill)) for atom in atoms.values()):
            kept.append(skill)
        else:
            unevidenced.append(skill)
    return kept, unevidenced


def _atom_supports(atom: EvidenceAtom, canonical: str) -> bool:
    if canonical.casefold() in {canonical_skill(s).casefold() for s in atom.skills}:
        return True
    haystack = " ".join(part for part in (atom.title, atom.description, atom.metrics or ""))
    return mentions_term(haystack, canonical)
