import pytest

from jobhunt.linkedin.fields import FIELDS, field_spec, over_limit, over_limit_message


def test_every_field_has_a_spec():
    names = [spec.name for spec in FIELDS]
    assert names == ["headline", "about", "experience", "projects", "skills"]


def test_kinds_are_the_three_verification_paths():
    # The registry is what dispatches verification, so an unknown kind here
    # would mean a field nothing checks.
    assert {spec.kind for spec in FIELDS} == {"prose", "bullets", "skills"}


def test_field_spec_looks_up_by_name():
    assert field_spec("about").limit == 2600


def test_field_spec_raises_on_unknown_name():
    with pytest.raises(KeyError, match="nickname"):
        field_spec("nickname")


def test_prose_at_the_limit_is_not_over_it():
    spec = field_spec("headline")
    assert over_limit(spec, "x" * 220) is False


def test_prose_one_character_over_is_over():
    spec = field_spec("headline")
    assert over_limit(spec, "x" * 221) is True


def test_skills_are_measured_in_items_not_characters():
    spec = field_spec("skills")
    # 60 short skills is far under any character count but over the item cap.
    assert over_limit(spec, ", ".join(f"skill{i}" for i in range(60))) is True


def test_one_overlong_skill_is_over_the_limit():
    spec = field_spec("skills")
    assert over_limit(spec, "Python, " + "x" * 81) is True


def test_a_normal_skills_line_is_not_over_the_limit():
    spec = field_spec("skills")
    assert over_limit(spec, "Python, Flask, Redis") is False


def test_skills_over_the_item_count_reports_the_item_count():
    spec = field_spec("skills")
    text = ", ".join(f"skill{i}" for i in range(60))
    assert over_limit_message(spec, text) == f"60 skills, LinkedIn allows {spec.limit}"


def test_skills_within_item_count_but_one_item_too_long_reports_the_item_limit():
    spec = field_spec("skills")
    text = "Python, " + "x" * (spec.item_limit + 1)
    assert (
        over_limit_message(spec, text) == f"One skill is longer than {spec.item_limit} characters"
    )


def test_skills_over_both_limits_reports_the_item_count_first():
    spec = field_spec("skills")
    long_skill = "x" * (spec.item_limit + 1)
    text = ", ".join([long_skill] * (spec.limit + 1))
    assert (
        over_limit_message(spec, text) == f"{spec.limit + 1} skills, LinkedIn allows {spec.limit}"
    )


def test_skills_within_both_limits_is_the_empty_string():
    spec = field_spec("skills")
    assert over_limit_message(spec, "Python, Flask, Redis") == ""


def test_a_prose_field_over_its_limit_reports_characters():
    spec = field_spec("headline")
    text = "x" * (spec.limit + 1)
    assert over_limit_message(spec, text) == f"{len(text)} of {spec.limit} characters"


def test_a_prose_field_within_its_limit_is_the_empty_string():
    spec = field_spec("headline")
    assert over_limit_message(spec, "x" * spec.limit) == ""
