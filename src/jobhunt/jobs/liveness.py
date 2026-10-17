"""Is a job posting still open? Ported from career-ops liveness-core (MIT, see NOTICE)."""

import re

import httpx

# Anti-bot interstitials: the page says nothing about the job, so never call it closed.
ANTI_BOT_MARKERS = ("cf-challenge", "just a moment...", "attention required", "captcha")

# Phrases that mean the posting is closed when stated as fact. Multilingual on purpose.
CLOSED_PHRASES = (
    "no longer accepting applications",
    "no longer available",
    "position has been filled",
    "job has expired",
    "this job has been closed",
    "position is closed",
    "no longer open",
    "n'est plus disponible",
    "nicht mehr verfügbar",
    "ya no está disponible",
    "já não está disponível",
    "niet meer beschikbaar",
)

# A closed phrase inside a conditional or future sentence ("will close when...") is not a closure.
CONDITION_RE = re.compile(r"\b(if|when|once|until|after|will|deadline)\b|closes on")

# Sentence boundaries, so a guard word in one sentence does not excuse a closed phrase in another.
SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]+")


def _closed_banner(text: str) -> bool:
    for sentence in SENTENCE_SPLIT_RE.split(text.casefold()):
        if any(p in sentence for p in CLOSED_PHRASES) and not CONDITION_RE.search(sentence):
            return True
    return False


def classify(status: int, original_url: str, final_url: str, text: str) -> str:
    if status in (404, 410):
        return "expired"
    if not 200 <= status < 300 or any(m in text.casefold() for m in ANTI_BOT_MARKERS):
        return "uncertain"
    # A redirect that drops the job id usually means the posting is gone, but we cannot tell.
    ids = re.findall(r"\d{4,}", original_url)
    if final_url != original_url and ids:
        longest = max(ids, key=len)
        if longest not in final_url:
            return "uncertain"
    if _closed_banner(text):
        return "expired"
    return "active"


def check_liveness(url: str, client: httpx.Client) -> str:
    try:
        resp = client.get(url, follow_redirects=True)
    except httpx.HTTPError:
        return "uncertain"
    return classify(resp.status_code, url, str(resp.url), resp.text)
