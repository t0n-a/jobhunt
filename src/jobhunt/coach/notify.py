import logging

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = 15


def render_task_line(today: dict) -> str:
    """One task, rendered for a terminal or a chat message.

    No streak, no guilt, no "you missed N days" — returning after a gap must
    cost nothing, or the gap becomes permanent.
    """
    step = today.get("step")
    if step is None:
        return "Nothing queued today. Pick a skill gap when you want the next one."
    lines = [
        f"Today ({today.get('minutes', 45)} min): {step.title}",
    ]
    if today.get("why"):
        lines.append(today["why"])
    if today.get("reward"):
        lines.append(f"Earns: {today['reward']}")
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    """Push one message. Returns False on any failure rather than raising —
    a failed notification must never break a scan or a scheduled run."""
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
        if response.status_code == 200:
            return True
        logger.warning("telegram send failed: HTTP %s", response.status_code)
        return False
    except httpx.HTTPError as exc:
        logger.warning("telegram send failed: %s", exc)
        return False
