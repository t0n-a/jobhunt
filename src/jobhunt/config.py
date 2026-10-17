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
    # Optional resume header lines. Omitted from the document when unset, so a
    # user without a LinkedIn does not get an empty separator on their CV.
    phone: str | None = None
    linkedin: str | None = None
    website: str | None = None


class LLMConfig(BaseModel):
    provider: Literal["agy", "openai_compat"] = "agy"
    model: str = "gemini-3.8-flash-medium"
    binary: str = "agy.exe"
    timeout_seconds: int = 300
    # Minimum gap between request starts; free API tiers cap requests per minute.
    min_interval_seconds: float = 0.0
    cache_dir: Path = Path("./data/llm_cache")


class JobSpyConfig(BaseModel):
    # Broad on purpose: triage drops senior titles before scoring.
    search_terms: list[str] = Field(
        default_factory=lambda: [
            "software engineer",
            "software developer",
            "backend developer",
            "frontend developer",
            "full stack developer",
            "python developer",
        ]
    )
    locations: list[str] = Field(default_factory=lambda: ["Portugal", "European Union", "Remote"])
    results_wanted: int = 50
    hours_old: int = 72
    country_indeed: str = "portugal"
    deadline_seconds: int = 600


class SourcesConfig(BaseModel):
    enabled: list[str] = Field(
        default_factory=lambda: [
            "linkedin",
            "indeed",
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
    jobspy: JobSpyConfig = Field(default_factory=JobSpyConfig)
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


class RankingConfig(BaseModel):
    # Languages the candidate works in; a posting requiring any other language is dropped.
    languages: list[str] = Field(default_factory=lambda: ["english", "portuguese"])
    # fit_score bands: below shortlist_fit is dropped, from strong_fit up is a strong match.
    shortlist_fit: int = 40
    strong_fit: int = 80
    # Outside the EU a job is "ok" only if it mentions sponsorship or pays at least this.
    min_pay_outside_eu: int = 60_000
    # Most postings to liveness-check per run, so a large backlog cannot stall a scan.
    liveness_check_limit: int = 50


class Config(BaseModel):
    profile: ProfileConfig
    llm: LLMConfig = Field(default_factory=LLMConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    data_dir: Path = Path("./data")
    answers_path: Path = Path("answers.yaml")


def load_config(path: Path) -> Config:
    """Load config.yaml. Raises FileNotFoundError if absent."""
    if not path.exists():
        raise FileNotFoundError(
            f"No config file at {path}. Copy config.example.yaml to config.yaml."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)
