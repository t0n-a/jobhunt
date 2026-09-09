from jobhunt.matching.skills import canonical_skill, mentions_term
from jobhunt.profile.models import AtomKind, EvidenceAtom

# A tagged skill is a claim the atom makes about itself. A keyword appearing in
# its prose may be incidental — "we considered Kubernetes and rejected it" — so
# it counts, but for less.
SKILL_WEIGHT = 2
TEXT_WEIGHT = 1


def shortlist_atoms(
    atoms: list[EvidenceAtom], keywords: list[str], *, limit: int = 25
) -> list[EvidenceAtom]:
    """Rank confirmed atoms by overlap with the posting's keywords.

    Deterministic and model-free on purpose: selection decides which of the
    user's facts are even eligible to appear, and a silently dropped atom is
    invisible to them. Ties break on atom id so two runs of the same tailoring
    produce the same candidate set.
    """
    wanted = {canonical_skill(k).casefold() for k in keywords if k}
    wanted.discard("")

    scored: list[tuple[int, int, EvidenceAtom]] = []
    education: list[EvidenceAtom] = []

    for atom in atoms:
        # The boundary: an unconfirmed atom is unreviewed model output and is
        # not eligible for a document an employer will read.
        if not atom.confirmed:
            continue
        if atom.kind is AtomKind.EDUCATION:
            education.append(atom)
            continue
        score = _score(atom, wanted)
        if score > 0:
            scored.append((score, -(atom.id or 0), atom))

    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    picked = [row[2] for row in scored[:limit]]

    # Kept regardless of score: an education atom rarely shares keywords with a
    # backend posting, and a resume with no education section reads as a bug.
    return education + picked


def _score(atom: EvidenceAtom, wanted: set[str]) -> int:
    tagged = {canonical_skill(s).casefold() for s in atom.skills}
    haystack = f"{atom.title} {atom.description}".casefold()

    score = 0
    for keyword in wanted:
        if keyword in tagged:
            score += SKILL_WEIGHT
        elif mentions_term(haystack, keyword):
            score += TEXT_WEIGHT
    return score
