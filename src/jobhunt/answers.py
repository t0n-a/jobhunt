"""Standard answers: the candidate's fixed form answers (work authorisation, notice, pay, EEO)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, model_validator


class StandardAnswers(BaseModel):
    work_authorization: str
    needs_sponsorship_outside_eu: bool
    notice_period: str
    salary_min_eur: int
    salary_max_eur: int
    relocation: bool
    remote: bool
    # EEO and diversity questions; "Prefer not to say" unless the candidate chooses otherwise.
    gender: str = "Prefer not to say"
    ethnicity: str = "Prefer not to say"
    disability: str = "Prefer not to say"
    veteran: str = "Prefer not to say"
    data_processing_consent: bool = True

    @model_validator(mode="after")
    def _min_not_above_max(self):
        if self.salary_min_eur > self.salary_max_eur:
            raise ValueError("salary_min_eur must not exceed salary_max_eur")
        return self


def load_answers(path: Path) -> StandardAnswers | None:
    """Load answers.yaml. Returns None if absent; names the file on bad content."""
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return StandardAnswers.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def save_answers(path: Path, answers: StandardAnswers) -> None:
    path.write_text(yaml.safe_dump(answers.model_dump(), sort_keys=False), encoding="utf-8")
