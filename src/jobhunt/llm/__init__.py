from jobhunt.config import LLMConfig
from jobhunt.llm.agy import AgyProvider
from jobhunt.llm.base import LLMError, LLMProvider
from jobhunt.llm.cache import CachingProvider
from jobhunt.llm.fake import FakeProvider
from jobhunt.llm.openai_compat import OpenAICompatProvider

__all__ = [
    "AgyProvider",
    "CachingProvider",
    "FakeProvider",
    "LLMError",
    "LLMProvider",
    "OpenAICompatProvider",
    "build_provider",
]


def build_provider(cfg: LLMConfig) -> LLMProvider:
    """Construct the configured provider, wrapped in the disk cache."""
    if cfg.provider == "agy":
        inner: LLMProvider = AgyProvider(
            model=cfg.model, binary=cfg.binary, timeout_seconds=cfg.timeout_seconds
        )
    else:
        import os

        base_url = os.environ.get("OPENAI_BASE_URL")
        api_key = os.environ.get("OPENAI_API_KEY")
        missing = []
        if not base_url:
            missing.append("OPENAI_BASE_URL")
        if not api_key:
            missing.append("OPENAI_API_KEY")
        if missing:
            vars_str = " and ".join(missing)
            raise LLMError(f"provider 'openai_compat' needs {vars_str} in .env")
        inner = OpenAICompatProvider(
            base_url=base_url,
            api_key=api_key,
            model=cfg.model,
            timeout_seconds=cfg.timeout_seconds,
        )
    return CachingProvider(inner, cfg.cache_dir)
