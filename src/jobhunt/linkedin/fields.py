"""The LinkedIn field registry: the one place a LinkedIn limit is written down.

LinkedIn publishes no canonical limits page, so these numbers come from
third-party references that agree with each other and will eventually be wrong.
Keeping them here means a correction is a one-line change, and `over_limit`
flags rather than truncates, so a stale number costs the user a spurious
warning instead of a severed sentence.
"""

from dataclasses import dataclass
from typing import Literal

FieldKind = Literal["prose", "bullets", "skills"]


@dataclass(frozen=True)
class FieldSpec:
    """One LinkedIn field.

    `kind` is also what dispatches verification: the three kinds are exactly
    the three checking paths, so a field cannot exist without something that
    checks it.

    `limit` is characters for "prose", characters per entry for "bullets", and
    a count of items for "skills". `item_limit` is the per-item character cap
    and is set for "skills" alone.
    """

    name: str
    kind: FieldKind
    limit: int
    item_limit: int | None = None


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("headline", "prose", 220),
    FieldSpec("about", "prose", 2600),
    FieldSpec("experience", "bullets", 2000),
    FieldSpec("projects", "bullets", 2000),
    FieldSpec("skills", "skills", 50, item_limit=80),
)

_BY_NAME = {spec.name: spec for spec in FIELDS}


def field_spec(name: str) -> FieldSpec:
    """Look up one field. Raises KeyError naming the known fields."""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(
            f"No LinkedIn field named {name!r}. Known fields: {', '.join(_BY_NAME)}"
        ) from None


def split_skills(text: str) -> list[str]:
    """The skills line as items. One definition, so the count that is checked
    is the same count the page displays."""
    return [item.strip() for item in text.split(",") if item.strip()]


def over_limit(spec: FieldSpec, text: str) -> bool:
    """Is this text longer than LinkedIn accepts for this field?

    Measured here, in Python, and never inferred from the prompt: a language
    model cannot count characters, so a limit stated in a prompt is a request.
    """
    if spec.kind == "skills":
        items = split_skills(text)
        if len(items) > spec.limit:
            return True
        return any(len(item) > spec.item_limit for item in items) if spec.item_limit else False
    return len(text) > spec.limit


def over_limit_message(spec: FieldSpec, text: str) -> str:
    """Say what actually overran, in the units that actually overran.

    Skills has two independent caps — how many items, and how long each one
    is — and they are counted in different units (items vs. characters). A
    message that always reports characters can name the wrong limit entirely
    for a skills field that is fine on item count but has one long entry.
    Reporting the item-count overrun first is arbitrary but fine: both can be
    true at once, and the user fixes one, sees the page again, fixes the other.
    """
    if spec.kind == "skills":
        items = split_skills(text)
        if len(items) > spec.limit:
            return f"{len(items)} skills, LinkedIn allows {spec.limit}"
        if spec.item_limit is not None and any(len(item) > spec.item_limit for item in items):
            return f"One skill is longer than {spec.item_limit} characters"
        return ""
    return f"{len(text)} of {spec.limit} characters"
