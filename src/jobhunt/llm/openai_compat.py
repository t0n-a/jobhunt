import time

import httpx

from jobhunt.llm.agy import _unwrap_response
from jobhunt.llm.base import LLMError

# Used when a 429 carries no usable Retry-After; capped so one bad header cannot stall a run.
DEFAULT_RETRY_AFTER_SECONDS = 30
MAX_RETRY_AFTER_SECONDS = 60


def _retry_after_seconds(response: httpx.Response) -> int:
    try:
        seconds = int(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    return min(max(seconds, 0), MAX_RETRY_AFTER_SECONDS)


class OpenAICompatProvider:
    """Any OpenAI-compatible chat-completions endpoint (OpenRouter, Gemini, …)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 120,
        min_interval_seconds: float = 0.0,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.min_interval_seconds = min_interval_seconds
        self._sleep = sleep
        self._clock = clock
        self._last_start: float | None = None

    def _pace(self) -> None:
        # Free tiers cap requests per minute; space request starts, not responses.
        if self._last_start is not None:
            wait = self.min_interval_seconds - (self._clock() - self._last_start)
            if wait > 0:
                self._sleep(wait)
        self._last_start = self._clock()

    def _post(self, body: dict) -> httpx.Response:
        self._pace()
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )

    def complete(self, prompt: str, schema: dict | None = None) -> str | dict:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if schema is not None:
            body["response_format"] = {"type": "json_object"}

        try:
            response = self._post(body)
            if response.status_code == 429:
                self._sleep(_retry_after_seconds(response))
                response = self._post(body)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"{self.model} request failed with HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"{self.model} request failed: {type(exc).__name__}") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("provider returned no message content") from exc

        if schema is None:
            return content
        try:
            return _unwrap_response(content)
        except LLMError:
            raise
        except ValueError as exc:
            raise LLMError(f"provider returned non-JSON: {content[:200]}") from exc
