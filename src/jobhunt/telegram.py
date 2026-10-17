import logging

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


_NEEDS_ID = {"yes", "no", "sent", "done", "replied", "interview", "offer", "rejected"}
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
    if verb in _NEEDS_ID and len(parts) == 2 and parts[1].isdigit():
        return verb, int(parts[1])
    return None


def queue_message(
    application, job, score, facts, web_url: str, note: str = ""
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
        text += f"\nAutopilot stopped: {note}"
    buttons = [
        [
            {"text": "Yes", "callback_data": f"yes:{application.id}"},
            {"text": "No", "callback_data": f"no:{application.id}"},
        ]
    ]
    return text, buttons
