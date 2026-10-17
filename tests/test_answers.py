from pathlib import Path

import pytest

from jobhunt.answers import StandardAnswers, load_answers, save_answers

ANSWERS = StandardAnswers(
    work_authorization="EU citizen; no sponsorship needed in the EU/EEA",
    needs_sponsorship_outside_eu=True,
    notice_period="2 weeks",
    salary_min_eur=45_000,
    salary_max_eur=60_000,
    relocation=False,
    remote=True,
)


def test_missing_file_is_none(tmp_path):
    assert load_answers(tmp_path / "answers.yaml") is None


def test_round_trip(tmp_path):
    path = tmp_path / "answers.yaml"
    save_answers(path, ANSWERS)
    assert load_answers(path) == ANSWERS


def test_example_file_loads():
    example = Path(__file__).parent.parent / "answers.example.yaml"
    assert load_answers(example) is not None


def test_min_above_max_is_refused(tmp_path):
    with pytest.raises(ValueError):
        StandardAnswers(**{**ANSWERS.model_dump(), "salary_min_eur": 70_000})


def test_bad_yaml_names_the_file(tmp_path):
    path = tmp_path / "answers.yaml"
    path.write_text(": : :", encoding="utf-8")
    with pytest.raises(ValueError, match="answers.yaml"):
        load_answers(path)
