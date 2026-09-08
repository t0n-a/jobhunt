import httpx

from jobhunt.llm.agy import _unwrap_response
from jobhunt.llm.base import LLMError


class OpenAICompatProvider:
    """Any OpenAI-compatible chat-completions endpoint (OpenRouter, Gemini, …)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def complete(self, prompt: str, schema: dict | None = None) -> str | dict:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if schema is not None:
            body["response_format"] = {"type": "json_object"}

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                )
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
