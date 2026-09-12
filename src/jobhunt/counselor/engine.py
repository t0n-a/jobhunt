"""One user message in, one visible counselor turn out.

The loop: build the briefing, ask the model for an action, run look-ups and
re-ask (at most two), then commit whatever terminal action came back. A
response the parser rejects gets one retry; a second failure is an apology the
user sees but the transcript never records. A model that keeps asking for look-ups
is cut off after MAX_LOOKUPS + 1 calls per attempt, so a turn never costs more
than PARSE_ATTEMPTS × (MAX_LOOKUPS + 1) calls.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, select

from jobhunt.config import Config
from jobhunt.counselor.actions import (
    ACTION_SCHEMA,
    Action,
    EndSitting,
    LookUp,
    Note,
    ProposeAtom,
    action_to_dict,
    parse_action,
    visible_text,
)
from jobhunt.counselor.briefing import build_briefing
from jobhunt.counselor.models import Conversation, CounselorNote, Turn
from jobhunt.counselor.prompt import render
from jobhunt.counselor.tools import run_tool
from jobhunt.llm import LLMError, LLMProvider

MAX_LOOKUPS = 2
WINDOW = 20
MAX_USER_CHARS = 4000
PARSE_ATTEMPTS = 2
APOLOGY = "I lost my train of thought — could you say that again?"
BUDGET_ERROR = "error: look-up budget spent — ask the user instead"


@dataclass(frozen=True)
class Reply:
    turn: Turn | None
    text: str
    user_turn: Turn | None


def _now() -> datetime:
    return datetime.now(UTC)


def get_or_create_conversation(session: Session) -> Conversation:
    conversation = session.exec(select(Conversation).order_by(Conversation.id)).first()
    if conversation is None:
        conversation = Conversation()
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
    return conversation


def recent_turns(session: Session, conversation: Conversation, limit: int = WINDOW) -> list[Turn]:
    newest_first = session.exec(
        select(Turn)
        .where(Turn.conversation_id == conversation.id)
        .order_by(Turn.id.desc())
        .limit(limit)
    ).all()
    return list(reversed(newest_first))


def _store_turn(session: Session, conversation: Conversation, turn: Turn) -> Turn:
    session.add(turn)
    conversation.last_turn_at = _now()
    session.add(conversation)
    session.commit()
    session.refresh(turn)
    return turn


def _write_note(
    session: Session, conversation: Conversation, note: Note, source_turn_id: int | None
) -> None:
    # A close with no matching open note is a silent no-op: the model may have reworded the thread.
    if note.close:
        for existing in session.exec(
            select(CounselorNote).where(
                CounselorNote.conversation_id == conversation.id,
                CounselorNote.kind == note.kind,
                CounselorNote.status == "open",
            )
        ).all():
            if existing.text.strip().casefold() == note.text.strip().casefold():
                existing.status = "closed"
                session.add(existing)
        return
    session.add(
        CounselorNote(
            conversation_id=conversation.id,
            kind=note.kind,
            text=note.text,
            source_turn_id=source_turn_id,
        )
    )


def _commit(
    session: Session, conversation: Conversation, action: Action, user_turn: Turn | None
) -> Turn:
    source_id = user_turn.id if user_turn is not None else None
    if isinstance(action, Note):
        _write_note(session, conversation, action, source_id)
    if isinstance(action, EndSitting) and action.next_time:
        session.add(
            CounselorNote(
                conversation_id=conversation.id,
                kind="thread",
                text=action.next_time,
                source_turn_id=source_id,
            )
        )
    visible = action.then if isinstance(action, Note) else action
    turn = Turn(
        conversation_id=conversation.id,
        role="counselor",
        text=visible_text(action),
        action=action_to_dict(action),
        proposal_state="open" if isinstance(visible, ProposeAtom) else None,
    )
    return _store_turn(session, conversation, turn)


def respond(session: Session, llm: LLMProvider, user_text: str | None, *, config: Config) -> Reply:
    if user_text is not None and len(user_text) > MAX_USER_CHARS:
        raise ValueError(f"Message is longer than {MAX_USER_CHARS} characters")

    conversation = get_or_create_conversation(session)
    # The briefing's "days since" and "first conversation" must describe the
    # state before this message, so capture the timestamp before storing it.
    previous_last_turn_at = conversation.last_turn_at
    user_turn: Turn | None = None
    if user_text is not None:
        user_turn = _store_turn(
            session,
            conversation,
            Turn(conversation_id=conversation.id, role="user", text=user_text.strip()),
        )

    snapshot = Conversation(
        id=conversation.id, created_at=conversation.created_at, last_turn_at=previous_last_turn_at
    )
    briefing = build_briefing(session, config=config, conversation=snapshot)
    turns = recent_turns(session, conversation)
    lookups: list[tuple[LookUp, str]] = []

    for _attempt in range(PARSE_ATTEMPTS):
        for _call in range(MAX_LOOKUPS + 1):
            raw = llm.complete(render(briefing, turns, lookups), schema=ACTION_SCHEMA)
            try:
                action = parse_action(raw)
            except LLMError:
                break  # to the next parse attempt
            if isinstance(action, LookUp):
                if len(lookups) < MAX_LOOKUPS:
                    lookups.append(
                        (action, run_tool(action.tool, action.args, session, config=config))
                    )
                else:
                    lookups.append((action, BUDGET_ERROR))
                continue
            turn = _commit(session, conversation, action, user_turn)
            return Reply(turn=turn, text=turn.text, user_turn=user_turn)

    return Reply(turn=None, text=APOLOGY, user_turn=user_turn)
