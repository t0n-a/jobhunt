from typing import Protocol


class LLMError(RuntimeError):
    """Raised when a provider cannot produce a response."""


class LLMProvider(Protocol):
    def complete(self, prompt: str, schema: dict | None = None) -> str | dict:
        """Return model output. If schema is given, returns parsed JSON."""
        ...
