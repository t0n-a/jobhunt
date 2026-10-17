import logging
from datetime import datetime

from sqlmodel import Session, select

from jobhunt.apply.followup import followups_due, mark_followup_done
from jobhunt.apply.queue import approve, autopilot_available, skip, trust_count
from jobhunt.apply.tracker import NEXT_STATUSES, set_status
from jobhunt.config import Config
from jobhunt.jobs.models import Job
from jobhunt.state import get_state, is_paused, set_paused, set_state
from jobhunt.tailor.models import Application
from jobhunt.telegram import TelegramClient, parse_command

logger = logging.getLogger(__name__)

HELP = (
    "Commands: yes N, no N (queue), sent N, replied N, interview N, offer N, rejected N, "
    "done N (follow-up sent), pause, resume, status."
)
SENDABLE = ("drafted", "queued", "approved")


def handle_command(
    session: Session, verb: str, arg: int | None, config: Config, now: datetime
) -> str:
    cfg = config.autopilot
    target = cfg.trust_target
    if verb == "help":
        return HELP
    if verb == "pause":
        set_paused(session, True)
        return "Paused. Nothing new is tailored or queued until you send resume."
    if verb == "resume":
        set_paused(session, False)
        return "Resumed."
    if verb == "status":
        queued = len(session.exec(select(Application).where(Application.status == "queued")).all())
        return (
            f"{'Paused' if is_paused(session) else 'Running'}. Queued: {queued}. "
            f"Follow-ups due: {len(followups_due(session, now))}. "
            f"Trust: {trust_count(session)}/{target}."
        )

    application = session.get(Application, arg)
    if application is None:
        return f"No application #{arg}."
    unchanged = f"#{arg} is {application.status}; nothing changed."

    if verb in ("yes", "no"):
        if application.status != "queued":
            return unchanged
        if verb == "no":
            skip(session, application)
            return f"Skipped #{arg}."
        before = autopilot_available(session, cfg)
        approve(session, application, cfg)
        job = session.get(Job, application.job_id)
        review = "PDF and answers" if application.pdf_path else "Review (no PDF yet; render it there)"
        reply = (
            f"Approved #{arg}. Apply here: {job.url}\n"
            f"{review}: {cfg.web_url}/applications/{arg}\n"
            f'Send "sent {arg}" once it\'s out.'
        )
        if application.reviewed_at is None:
            reply += "\nOpen the review page before approving to count toward trust."
        if not before and autopilot_available(session, cfg):
            reply += f"\nTrust {trust_count(session)}/{target}: autopilot can now be switched on."
        return reply
    if verb == "sent":
        if application.status not in SENDABLE:
            return unchanged
        set_status(session, application, "sent", now=now)
        return f"Marked #{arg} as sent."
    if verb in NEXT_STATUSES:
        if application.status not in NEXT_STATUSES[verb]:
            return unchanged
        set_status(session, application, verb, now=now)
        return f"Marked #{arg} as {verb}."
    # verb == "done"
    if any(f.application_id == arg for f in followups_due(session, now)):
        mark_followup_done(session, application, now=now)
        return f"Noted the follow-up on #{arg}."
    return f"Nothing due for #{arg}."


def run_tick(session: Session, client: TelegramClient, config: Config, now: datetime) -> int:
    stored = get_state(session, "telegram_offset")
    handled = 0
    for update in client.updates(int(stored) if stored else None):
        # Advance first: a command that crashes must not be replayed forever.
        set_state(session, "telegram_offset", str(update["update_id"] + 1))
        callback = update.get("callback_query")
        if callback:
            message, text = callback.get("message") or {}, callback.get("data") or ""
            client.answer_callback(callback["id"])
        else:
            message, text = update.get("message") or {}, update.get("message", {}).get("text") or ""
        if str(message.get("chat", {}).get("id")) != client.chat_id:
            continue
        handled += 1
        parsed = parse_command(text)
        if parsed is None:
            reply = HELP
        else:
            try:
                reply = handle_command(session, parsed[0], parsed[1], config, now)
            except Exception:
                logger.exception("command failed: %s %s", *parsed)
                reply = f"That didn't work: {parsed[0]} {parsed[1]}. Try again or check the web UI."
        client.send(reply)
    return handled
