"""From confirmed atoms to one persisted, unapproved version.

The order below is the safety argument, which is why it lives here and not in a
request handler: load, refuse if there is nothing to draw on, derive keywords,
shortlist, compose, VERIFY, measure, and only then write. Verification precedes
the single commit, so the database cannot hold a field that was never checked.
"""

from sqlmodel import Session

from jobhunt.config import ProfileConfig
from jobhunt.linkedin.compose import compose_profile
from jobhunt.linkedin.fields import field_spec, over_limit
from jobhunt.linkedin.keywords import target_keywords
from jobhunt.linkedin.models import LinkedinField, LinkedinProfile
from jobhunt.linkedin.store import LinkedinStore
from jobhunt.llm import LLMProvider
from jobhunt.matching.score import EmptyProfileError
from jobhunt.profile.models import EvidenceAtom
from jobhunt.profile.store import AtomStore
from jobhunt.tailor.select import shortlist_atoms
from jobhunt.tailor.verify import verify_bullet, verify_prose, verify_skills

# A profile covers a career rather than one posting, so it draws on more
# evidence than a tailored resume does.
SHORTLIST_LIMIT = 40


def build_profile(
    session: Session,
    llm: LLMProvider,
    *,
    profile: ProfileConfig,
    limit: int = SHORTLIST_LIMIT,
) -> LinkedinProfile:
    """Build one new version. Never edits an existing one."""
    atoms = AtomStore(session).confirmed_only()
    if not atoms:
        raise EmptyProfileError(
            "Cannot build a LinkedIn profile with no confirmed evidence. Confirm at "
            "least one atom first — run the interview or import a CV, then confirm."
        )

    keywords, keyword_source = target_keywords(session)
    shortlist = shortlist_atoms(atoms, keywords, limit=limit) or atoms[:limit]
    document = compose_profile(shortlist, keywords, llm, profile=profile)

    by_id = {atom.id: atom for atom in shortlist}
    fields, dropped = _verify(document, by_id)

    version = LinkedinProfile(
        keywords=keywords,
        keyword_source=keyword_source,
        atom_ids=sorted(by_id),
        dropped=dropped,
    )
    return LinkedinStore(session).add_version(version, fields)


def _verify(
    document: dict, by_id: dict[int, EvidenceAtom]
) -> tuple[list[LinkedinField], list[dict]]:
    """Check every field by its registry kind and build the rows.

    A field that fails is kept as a row with EMPTY text rather than omitted: an
    absent field is indistinguishable from one the user has already pasted, and
    the reject is what explains the gap.
    """
    fields: list[LinkedinField] = []
    dropped: list[dict] = []
    shortlist_ids = set(by_id)

    for name in ("headline", "about"):
        text = document.get(name, "")
        result = verify_prose(text, list(by_id.values())) if text else None
        if text and not result.passed:
            dropped.append({"field": name, "text": text, "reason": result.reason})
            text = ""
        fields.append(_row(name, text))

    for name in ("experience", "projects"):
        position = 0
        for entry in document.get(name) or []:
            atom = by_id.get(entry["atom_id"])
            result = verify_bullet(entry, atom, shortlist_ids)
            if not result.passed:
                dropped.append({"field": name, "text": entry["text"], "reason": result.reason})
                continue
            fields.append(
                _row(
                    name,
                    entry["text"],
                    entry_title=entry.get("title", ""),
                    atom_id=entry["atom_id"],
                    position=position,
                )
            )
            position += 1

    kept, unevidenced = verify_skills(document.get("skills") or [], by_id)
    dropped.extend(
        {"field": "skills", "text": skill, "reason": "No evidence names it."}
        for skill in unevidenced
    )
    fields.append(_row("skills", ", ".join(kept)))

    return fields, dropped


def _row(name: str, text: str, **kwargs) -> LinkedinField:
    """One field row, with its limit measured here in Python.

    Measured rather than trusted: the prompt states the limit, but a language
    model cannot count characters, so the stated limit is a request. Over-length
    text is FLAGGED and kept whole — truncating guarantees a pasteable field by
    severing a sentence mid-word, which is worse than a warning on text the user
    is about to read anyway.
    """
    return LinkedinField(
        profile_id=0,  # set by LinkedinStore.add_version
        name=name,
        text=text,
        over_limit=over_limit(field_spec(name), text),
        **kwargs,
    )
