"""What to aim the profile at, when there is no job posting to read.

Tailoring takes its keywords from the description of one posting. A profile has
none, so they come from the roles the user is actually pursuing: the keywords
already extracted for their applications. Both derivations here are
deterministic and model-free, so the same store produces the same keywords
twice and the shortlist below them does not wander between runs.
"""

from collections import Counter

from sqlmodel import Session, select

from jobhunt.matching.skills import canonical_skill
from jobhunt.profile.models import EvidenceAtom
from jobhunt.tailor.models import Application


def target_keywords(session: Session, *, limit: int = 25) -> tuple[list[str], str]:
    """Return the target keywords and which derivation produced them.

    ("applications", the preferred path) counts the ATS keywords across the
    user's applications. Those are kept in the posting's own spelling rather
    than canonicalised, because they are what employers wrote and the profile
    is read by the same people.

    ("atoms", the fallback) counts the skills on confirmed atoms, canonicalised
    so "AWS" and "Amazon Web Services" do not split one skill into two
    weaker entries.
    """
    counts: Counter[str] = Counter()
    for application in session.exec(select(Application)).all():
        for keyword in application.keywords or []:
            if isinstance(keyword, str) and keyword.strip():
                counts[keyword.strip()] += 1
    if counts:
        return [keyword for keyword, _ in counts.most_common(limit)], "applications"

    skills: Counter[str] = Counter()
    confirmed = session.exec(select(EvidenceAtom).where(EvidenceAtom.confirmed)).all()
    for atom in confirmed:
        for skill in atom.skills or []:
            if isinstance(skill, str) and skill.strip():
                skills[canonical_skill(skill)] += 1
    return [skill for skill, _ in skills.most_common(limit)], "atoms"
