from jobhunt.config import LLMConfig
from jobhunt.llm.agy import AgyProvider
from jobhunt.llm.base import LLMError, LLMProvider
from jobhunt.llm.cache import CachingProvider
from jobhunt.llm.fake import FakeProvider

__all__ = [
    "AgyProvider",
    "CachingProvider",
    "FakeProvider",
    "LLMError",
    "LLMProvider",
    "build_provider",
]


def build_provider(cfg: LLMConfig) -> LLMProvider:
    """Construct the configured provider, wrapped in the disk cache."""
    if cfg.provider == "agy":
        inner: LLMProvider = AgyProvider(
            model=cfg.model, binary=cfg.binary, timeout_seconds=cfg.timeout_seconds
        )
    else:
        raise NotImplementedError(
            "openai_compat provider arrives in Phase 2; use provider: agy for now"
        )
    return CachingProvider(inner, cfg.cache_dir)
