"""The one JSON shape the counselor model may answer with, and its parsing.

The provider has no tool calling; every turn the model returns one of these
actions and the engine does the rest. Parsing is strict: a shape the engine
would have to guess at is an LLMError, which the engine treats as retryable.
"""

from dataclasses import dataclass

from jobhunt.counselor.models import NOTE_KINDS
from jobhunt.llm import LLMError
from jobhunt.profile.models import AtomKind

MAX_QUICK_REPLIES = 4
MAX_QUICK_REPLY_CHARS = 40
TOOL_NAMES = ("atom", "atoms_with_skill", "gap", "repo", "job")

_ATOM_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": [k.value for k in AtomKind]},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "skills": {"type": "array", "items": {"type": "string"}},
        "metrics": {"type": ["string", "null"]},
    },
    "required": ["kind", "title", "description", "skills"],
}

_VISIBLE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["ask", "propose_atom"]},
        "text": {"type": "string"},
        "quick_replies": {"type": "array", "items": {"type": "string"}},
        "atom": _ATOM_SCHEMA,
        "because": {"type": "string"},
    },
    "required": ["action", "text"],
}

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["ask", "look_up", "propose_atom", "note", "end_sitting"],
        },
        "text": {"type": "string"},
        "quick_replies": {"type": "array", "items": {"type": "string"}},
        "tool": {"type": "string", "enum": list(TOOL_NAMES)},
        "args": {"type": "object"},
        "atom": _ATOM_SCHEMA,
        "because": {"type": "string"},
        "kind": {"type": "string", "enum": list(NOTE_KINDS)},
        "close": {"type": "boolean"},
        "then": _VISIBLE_SCHEMA,
        "summary": {"type": "string"},
        "next_time": {"type": ["string", "null"]},
    },
    "required": ["action"],
}


@dataclass(frozen=True)
class Ask:
    text: str
    quick_replies: tuple[str, ...] = ()


@dataclass(frozen=True)
class LookUp:
    tool: str
    args: dict


@dataclass(frozen=True)
class AtomDraft:
    kind: str
    title: str
    description: str
    skills: tuple[str, ...]
    metrics: str | None


@dataclass(frozen=True)
class ProposeAtom:
    text: str
    atom: AtomDraft
    because: str


@dataclass(frozen=True)
class Note:
    kind: str
    text: str
    close: bool
    then: "Ask | ProposeAtom"


@dataclass(frozen=True)
class EndSitting:
    summary: str
    next_time: str | None


Action = Ask | LookUp | ProposeAtom | Note | EndSitting


def _text(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LLMError(f"Counselor action missing text field {key!r}")
    return value.strip()


def _quick_replies(raw: dict) -> tuple[str, ...]:
    replies = raw.get("quick_replies") or []
    if not isinstance(replies, list):
        raise LLMError("quick_replies must be a list")
    kept = [
        r.strip()
        for r in replies[:MAX_QUICK_REPLIES]
        if isinstance(r, str) and 0 < len(r.strip()) <= MAX_QUICK_REPLY_CHARS
    ]
    return tuple(kept[:MAX_QUICK_REPLIES])


def _atom(raw: dict) -> AtomDraft:
    atom = raw.get("atom")
    if not isinstance(atom, dict):
        raise LLMError("propose_atom needs an atom object")
    kind = atom.get("kind")
    if kind not in {k.value for k in AtomKind}:
        raise LLMError(f"Unknown atom kind {kind!r}")
    skills = atom.get("skills") or []
    if not isinstance(skills, list):
        raise LLMError("atom.skills must be a list")
    metrics = atom.get("metrics")
    return AtomDraft(
        kind=kind,
        title=_text(atom, "title"),
        description=_text(atom, "description"),
        skills=tuple(s.strip() for s in skills if isinstance(s, str) and s.strip()),
        metrics=metrics.strip() if isinstance(metrics, str) and metrics.strip() else None,
    )


def parse_action(raw: object) -> Action:
    if not isinstance(raw, dict):
        raise LLMError("Counselor action must be a JSON object")
    name = raw.get("action")
    if name == "ask":
        return Ask(_text(raw, "text"), _quick_replies(raw))
    if name == "look_up":
        tool = raw.get("tool")
        if tool not in TOOL_NAMES:
            raise LLMError(f"Unknown tool {tool!r}")
        args = raw.get("args") or {}
        if not isinstance(args, dict):
            raise LLMError("look_up args must be an object")
        return LookUp(tool, args)
    if name == "propose_atom":
        return ProposeAtom(_text(raw, "text"), _atom(raw), _text(raw, "because"))
    if name == "note":
        kind = raw.get("kind")
        if kind not in NOTE_KINDS:
            raise LLMError(f"Unknown note kind {kind!r}")
        then = parse_action(raw.get("then"))
        if not isinstance(then, (Ask, ProposeAtom)):
            raise LLMError("A note must be followed by an ask or a propose_atom")
        return Note(kind, _text(raw, "text"), bool(raw.get("close", False)), then)
    if name == "end_sitting":
        next_time = raw.get("next_time")
        return EndSitting(
            _text(raw, "summary"),
            next_time.strip() if isinstance(next_time, str) and next_time.strip() else None,
        )
    raise LLMError(f"Unknown counselor action {name!r}")


def action_to_dict(action: Action) -> dict:
    """The stored form. Inverse of parse_action for every valid action."""
    if isinstance(action, Ask):
        return {"action": "ask", "text": action.text, "quick_replies": list(action.quick_replies)}
    if isinstance(action, LookUp):
        return {"action": "look_up", "tool": action.tool, "args": dict(action.args)}
    if isinstance(action, ProposeAtom):
        atom = action.atom
        return {
            "action": "propose_atom",
            "text": action.text,
            "atom": {
                "kind": atom.kind,
                "title": atom.title,
                "description": atom.description,
                "skills": list(atom.skills),
                "metrics": atom.metrics,
            },
            "because": action.because,
        }
    if isinstance(action, Note):
        return {
            "action": "note",
            "kind": action.kind,
            "text": action.text,
            "close": action.close,
            "then": action_to_dict(action.then),
        }
    if isinstance(action, EndSitting):
        return {"action": "end_sitting", "summary": action.summary, "next_time": action.next_time}
    raise TypeError(f"Not an action: {action!r}")


def visible_text(action: Action) -> str:
    """What the chat shows for a terminal action."""
    if isinstance(action, Note):
        return visible_text(action.then)
    if isinstance(action, EndSitting):
        return action.summary
    if isinstance(action, (Ask, ProposeAtom)):
        return action.text
    raise TypeError(f"{type(action).__name__} has no visible text")
