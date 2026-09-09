"""What changed in one field since the previous version.

Word-level rather than character-level: a character diff of rewritten prose is
unreadable confetti. The purpose is narrow — let the user re-paste only the
fields that actually moved, instead of rereading five sections to find the one
new sentence.
"""

import difflib


def diff_field(previous: str, current: str) -> list[tuple[str, str]]:
    """Return ("same" | "added" | "removed", text) segments, in reading order.

    A field with no previous version has nothing to compare against and comes
    back as a single "same" segment, so the template needs no special case.
    """
    if not previous:
        return [("same", current)] if current else []

    old, new = previous.split(), current.split()
    segments: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new).get_opcodes():
        if tag == "equal":
            segments.append(("same", " ".join(new[j1:j2])))
        elif tag == "insert":
            segments.append(("added", " ".join(new[j1:j2])))
        elif tag == "delete":
            segments.append(("removed", " ".join(old[i1:i2])))
        else:  # "replace" — show what went and what came, in that order
            segments.append(("removed", " ".join(old[i1:i2])))
            segments.append(("added", " ".join(new[j1:j2])))
    return segments
