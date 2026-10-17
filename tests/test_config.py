import os
from pathlib import Path

import pytest

from jobhunt.cli import load_env
from jobhunt.config import load_config


def test_load_config_reads_profile_and_llm(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        """
profile:
  name: Ada Lovelace
  email: ada@example.com
  location: Porto, Portugal
  github_username: ada
llm:
  provider: agy
  model: gemini-3.8-flash-medium
data_dir: ./data
""",
        encoding="utf-8",
    )

    cfg = load_config(tmp_path / "config.yaml")

    assert cfg.profile.name == "Ada Lovelace"
    assert cfg.profile.github_username == "ada"
    assert cfg.llm.provider == "agy"
    assert cfg.llm.model == "gemini-3.8-flash-medium"
    assert cfg.llm.timeout_seconds == 300


def test_load_config_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_github_username_is_optional(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        """
profile:
  name: Ada
  email: ada@example.com
  location: Porto
llm: {}
""",
        encoding="utf-8",
    )

    cfg = load_config(tmp_path / "config.yaml")

    assert cfg.profile.github_username is None
    assert cfg.llm.provider == "agy"


def test_env_file_is_loaded(tmp_path: Path, monkeypatch):
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text("TEST_NEW_VAR=hello\nTEST_EXISTING_VAR=from_file\n", encoding="utf-8")
    monkeypatch.setenv("TEST_EXISTING_VAR", "pre_existing")
    monkeypatch.chdir(tmp_path)

    load_env()

    assert os.environ.get("TEST_NEW_VAR") == "hello"
    assert os.environ.get("TEST_EXISTING_VAR") == "pre_existing"


def test_ranking_defaults(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        """
profile:
  name: Ada
  email: ada@example.com
  location: Porto
""",
        encoding="utf-8",
    )

    cfg = load_config(tmp_path / "config.yaml")

    assert cfg.ranking.shortlist_fit == 40
    assert cfg.ranking.strong_fit == 80
    assert cfg.ranking.languages == ["english", "portuguese"]


def test_linkedin_and_indeed_enabled_by_default():
    from jobhunt.config import SourcesConfig

    assert {"linkedin", "indeed"} <= set(SourcesConfig().enabled)
