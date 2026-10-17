import logging
import re

import httpx

from jobhunt.ranking.shortlist import _clean

# httpx logs request URLs at INFO and the bot token is in the URL.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

API = "https://api.telegram.org"
MAX_TEXT = 4096  # Telegram's message limit


class TelegramClient:
    def __init__(self, token: str, chat_id: str, http: httpx.Client):
        self.token = token
        self.chat_id = chat_id
        self.http = http

    def _url(self, method: str) -> str:
        return f"{API}/bot{self.token}/{method}"

    def send(self, text: str, buttons: list[list[dict]] | None = None) -> bool:
        # No parse_mode: plain text, so a job title cannot inject markup.
        body: dict = {"chat_id": self.chat_id, "text": text[:MAX_TEXT]}
        if buttons:
            body["reply_markup"] = {"inline_keyboard": buttons}
        try:
            response = self.http.post(self._url("sendMessage"), json=body)
            if response.status_code == 200:
                return True
            logger.warning("telegram send failed: HTTP %s", response.status_code)
        except httpx.HTTPError as exc:
            logger.warning("telegram send failed: %s", type(exc).__name__)
        return False

    def updates(self, offset: int | None) -> list[dict]:
        body = {"offset": offset, "timeout": 0, "allowed_updates": ["message", "callback_query"]}
        try:
            response = self.http.post(self._url("getUpdates"), json=body)
            data = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("telegram getUpdates failed: %s", type(exc).__name__)
            return []
        return data.get("result", []) if data.get("ok") else []

    def answer_callback(self, callback_id: str) -> None:
        try:
            self.http.post(self._url("answerCallbackQuery"), json={"callback_query_id": callback_id})
        except httpx.HTTPError:
            pass


def discover_chat_id(token: str, http: httpx.Client, code: str) -> str | None:
    """The private chat that sent exactly `code`: anyone can message a bot, so the newest
    message alone could bind a stranger's chat as the one the bot obeys."""
    updates = TelegramClient(token, "", http).updates(None)
    for update in sorted(updates, key=lambda u: u.get("update_id", 0), reverse=True):
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") == "private" and (message.get("text") or "").strip() == code:
            return str(chat["id"])
    return None


_NEEDS_ID = {"yes", "no", "sent", "done", "replied", "interview", "offer", "rejected", "osent", "oskip", "oreplied", "publish", "approve", "decline"}
_NO_ARG = {"pause", "resume", "status", "help"}


def parse_command(text: str) -> tuple[str, int | str | None] | None:
    parts = text.strip().lstrip("/").replace(":", " ").split()
    if not parts:
        return None
    verb = parts[0].split("@")[0].lower()
    if verb in _NO_ARG and len(parts) == 1:
        return verb, None
    if verb == "autopilot" and len(parts) == 2 and parts[1].lower() in ("on", "off"):
        return verb, parts[1].lower()
    if verb == "unblock" and len(parts) == 2 and re.fullmatch(r"[a-z0-9.-]+", parts[1].lower()):
        return verb, parts[1].lower()
    if verb in _NEEDS_ID and len(parts) == 2 and parts[1].isdigit():
        return verb, int(parts[1])
    return None


def queue_message(
    application, job, score, facts, web_url: str, note: str = "", extra: str = ""
) -> tuple[str, list[list[dict]]]:
    pay = "pay ?"
    if facts is not None and facts.pay_yearly_eur:
        pay = f"€{facts.pay_yearly_eur:,}" + (" (est.)" if facts.pay_estimated else "")
    fit = "?" if score is None or score.fit_score is None else score.fit_score
    text = (
        f"#{application.id} {_clean(job.title)} @ {_clean(job.company)}\n"
        f"{pay} | fit {fit} | {_clean(job.location) or 'location ?'}\n"
        f"{job.url}\n"
        f"Review: {web_url}/applications/{application.id}"
    )
    if note:
        text += f"\nAutopilot: {_clean(note).removeprefix('autopilot ')[:200]}"  # reasons quote page labels
    if extra:
        # _clean strips newlines too, so each line is cleaned on its own.
        text += "\n\n" + "\n".join(_clean(line) for line in extra.split("\n"))[:1200]
    buttons = [
        [
            {"text": "Yes", "callback_data": f"yes:{application.id}"},
            {"text": "No", "callback_data": f"no:{application.id}"},
        ]
    ]
    return text, buttons


COPY_LIMIT = 256  # Telegram's copy_text button limit


def _clean_lines(text: str) -> str:
    """_clean per line: it strips newlines too, and drafts keep their paragraphs."""
    return "\n".join(_clean(line) for line in text.split("\n"))


def outreach_message(outreach, job, contact, web_url: str) -> list[tuple[str, list[list[dict]] | None]]:
    """Header with buttons, then the draft alone so a long-press copies it cleanly."""
    name = _clean(contact.name or "") or "the talent team"
    to = f"{name}, {_clean(contact.role)}" if contact.role else name
    lines = [
        f"Outreach #{outreach.id} ({outreach.kind}): {_clean(job.title)} @ {_clean(job.company)}",
        f"To: {to}",
    ]
    if contact.profile_url:
        lines.append(_clean(contact.profile_url))
    if getattr(contact, "email", None):
        lines.append(f"Email: {_clean(contact.email)}")
    lines.append("Copy the text below, send it yourself, then tap Sent.")
    text = _clean_lines(outreach.text)  # model output can echo page text
    buttons: list[list[dict]] = []
    if len(text) <= COPY_LIMIT:
        buttons.append([{"text": "Copy", "copy_text": {"text": text}}])
    buttons.append(
        [
            {"text": "Sent", "callback_data": f"osent:{outreach.id}"},
            {"text": "Skip", "callback_data": f"oskip:{outreach.id}"},
        ]
    )
    return [("\n".join(lines), buttons), (text, None)]


def growth_message(s) -> str:
    """One profile suggestion as plain text; repo and README text is cleaned per line."""
    if s.kind == "publish":
        lines = [f"#{s.id} {s.title}", *(f"  ⚠ {f}" for f in s.flags or [])]
        lines.append("Blocked until fixed." if s.blocking else f"publish {s.id} / decline {s.id}")
        return _clean_lines("\n".join(lines))
    if s.kind in ("readme", "profile_readme"):
        # shortcut: the marker string is duplicated from growth.readme.REPLACES; import it if a third reader appears.
        replaces = "replaces existing README" in (s.flags or [])
        notes = [f"⚠ {f}" for f in (s.flags or []) if f != "replaces existing README"]
        head = f"#{s.id} README draft for {s.target}" + (" (replaces your current README)" if replaces else "")
        return _clean_lines("\n".join([head, *notes, "", s.body, "", f"approve {s.id} / decline {s.id}"]))
    if s.kind == "pin":
        return _clean(f"#{s.id} Pin {s.target} on your GitHub profile: {s.body}")
    return _clean_lines(f"#{s.id} Certification: {s.title}. {s.body}")
