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
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
            if isinstance(cached, dict) and "response" in cached:
                return cached["response"]

        response = self._inner.complete(prompt, schema)
        payload = json.dumps({"response": response})
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self._dir, delete=False, suffix=".tmp", encoding="utf-8"
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(payload)
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise
        return response

    def _key(self, prompt: str, schema: dict | None) -> str:
        model = getattr(self._inner, "model", "unknown")
        payload = json.dumps({"model": model, "prompt": prompt, "schema": schema}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
