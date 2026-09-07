import hashlib
import json
import os
import tempfile
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
            try:
                return json.loads(path.read_text(encoding="utf-8"))["response"]
            except (OSError, ValueError, KeyError):
                pass

        response = self._inner.complete(prompt, schema)
        payload = json.dumps({"response": response})
        with tempfile.NamedTemporaryFile(
            mode="w", dir=self._dir, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
        return response

    def _key(self, prompt: str, schema: dict | None) -> str:
        model = getattr(self._inner, "model", "unknown")
        payload = json.dumps(
            {"model": model, "prompt": prompt, "schema": schema}, sort_keys=True
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
