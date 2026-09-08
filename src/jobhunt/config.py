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


class SourcesConfig(BaseModel):
    enabled: list[str] = Field(
        default_factory=lambda: [
            "greenhouse",
            "lever",
            "ashby",
            "remotive",
            "remoteok",
            "weworkremotely",
            "landingjobs",
            "hackernews",
        ]
    )
    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)
    location_keywords: list[str] = Field(default_factory=lambda: ["porto", "portugal", "remote"])
    # Hacker News comments are free prose, so a location keyword alone matches
    # every remote job of any kind — a live scan pulled in Sales, Copywriting and
    # Freelance Writing roles. A posting must look like engineering work too.
    role_keywords: list[str] = Field(
        default_factory=lambda: [
            "engineer",
            "developer",
            "programmer",
            "software",
            "backend",
            "back-end",
            "frontend",
            "front-end",
            "fullstack",
            "full-stack",
            "devops",
            "platform",
            "sre",
            "data",
            "machine learning",
            "ml ",
            "python",
            "java",
            "golang",
            "typescript",
        ]
    )


class Config(BaseModel):
    profile: ProfileConfig
    llm: LLMConfig = Field(default_factory=LLMConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    data_dir: Path = Path("./data")


def load_config(path: Path) -> Config:
    """Load config.yaml. Raises FileNotFoundError if absent."""
    if not path.exists():
        raise FileNotFoundError(
            f"No config file at {path}. Copy config.example.yaml to config.yaml."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)
