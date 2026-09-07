import hashlib
import json
from pathlib import Path

from jobhunt.llm.base import LLMProvider


class CachingProvider:
    """Wraps a provider with an on-disk cache keyed on model+prompt+schema."""

    def __init__(self, inner: LLMProvider, cache_dir: Path):
        self._inner = inner
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def complete(self, prompt: str, schema: dict | None = None) -> str | dict:
        key = self._key(prompt, schema)
        path = self._dir / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["response"]

        response = self._inner.complete(prompt, schema)
        path.write_text(json.dumps({"response": response}), encoding="utf-8")
        return response

    def _key(self, prompt: str, schema: dict | None) -> str:
        model = getattr(self._inner, "model", "unknown")
        payload = json.dumps(
            {"model": model, "prompt": prompt, "schema": schema}, sort_keys=True
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
