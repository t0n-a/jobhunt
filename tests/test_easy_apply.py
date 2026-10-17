from types import SimpleNamespace

import pytest

from jobhunt.answers import StandardAnswers
from jobhunt.apply.easy_apply import easy_apply_brief, is_linkedin_job


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.linkedin.com/jobs/view/123", True),
        ("https://linkedin.com/jobs/view/123/", True),
        ("https://linkedin.com/jobs/search/?keywords=dev", False),
        ("https://evil.com/linkedin.com/jobs/view/123", False),
        ("https://linkedin.com.evil.com/jobs/view/123", False),
        ("https://evillinkedin.com/jobs/view/123", False),
        ("https://linkedin.com@evil.com/jobs/view/123", False),
        ("https://user@www.linkedin.com/jobs/view/123", False),
        ("https://linkedin.com\\@evil.com/jobs/view/123", False),
        ("ftp://www.linkedin.com/jobs/view/123", False),
        ("", False),
    ],
)
def test_is_linkedin_job(url, expected):
    assert is_linkedin_job(url) is expected


ANSWERS = StandardAnswers(
    work_authorization="EU citizen",
    needs_sponsorship_outside_eu=False,
    notice_period="1 month",
    salary_min_eur=60000,
    salary_max_eur=75000,
    relocation=True,
    remote=False,
)


def test_brief_lists_standard_answers():
    app = SimpleNamespace(id=7)
    job = SimpleNamespace(title="Dev", company="Acme", url="https://www.linkedin.com/jobs/view/1")
    text = easy_apply_brief(app, job, ANSWERS, "http://w")
    assert text.startswith(
        "LinkedIn Easy Apply: you click Apply on LinkedIn; jobhunt never touches your account."
    )
    assert "CV: http://w/applications/7" in text
    assert "Answers you may be asked:" in text
    assert "EU citizen" in text and "1 month" in text
    assert "60,000" in text and "75,000" in text
    assert len(text) <= 1200


def test_brief_without_answers():
    app = SimpleNamespace(id=7)
    job = SimpleNamespace(title="Dev", company="Acme", url="https://www.linkedin.com/jobs/view/1")
    text = easy_apply_brief(app, job, None, "http://w")
    assert "(no answers.yaml yet: run jobhunt setup)" in text
    assert "CV: http://w/applications/7" in text
