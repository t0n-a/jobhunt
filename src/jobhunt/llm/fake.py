from jobhunt.llm.base import LLMError


class FakeProvider:
    """Test double. Returns queued responses in order and records prompts."""

    def __init__(self, responses: list[str | dict]):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt: str, schema: dict | None = None) -> str | dict:
        self.prompts.append(prompt)
        if not self._responses:
            raise LLMError("FakeProvider ran out of queued responses")
        return self._responses.pop(0)
