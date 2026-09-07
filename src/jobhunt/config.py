"""Configuration loading. All user-specific values live here, never in code."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class ProfileConfig(BaseModel):
    name: str
    email: str
    location: str
    github_username: str | None = None


class LLMConfig(BaseModel):
    provider: Literal["agy", "openai_compat"] = "agy"
    model: str = "gemini-3.8-flash-medium"
    binary: str = "agy.exe"
    timeout_seconds: int = 300
    cache_dir: Path = Path("./data/llm_cache")


class Config(BaseModel):
    profile: ProfileConfig
    llm: LLMConfig = Field(default_factory=LLMConfig)
    data_dir: Path = Path("./data")


def load_config(path: Path) -> Config:
    """Load config.yaml. Raises FileNotFoundError if absent."""
    if not path.exists():
        raise FileNotFoundError(
            f"No config file at {path}. Copy config.example.yaml to config.yaml."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)
