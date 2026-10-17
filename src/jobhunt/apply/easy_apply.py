"""LinkedIn Easy Apply: the candidate applies on LinkedIn himself; jobhunt only writes the brief."""

from __future__ import annotations

from urllib.parse import urlsplit

from jobhunt.answers import StandardAnswers
from jobhunt.ranking.shortlist import _clean


def is_linkedin_job(url: str) -> bool:
    # Same spoof checks as detect_ats: a browser must not read the host as something else.
    if not url or "\\" in url:
        return False
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return False
    host = parts.hostname or ""
    return (host == "linkedin.com" or host.endswith(".linkedin.com")) and parts.path.startswith(
        "/jobs/view/"
    )


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def easy_apply_brief(
    application, job, answers: StandardAnswers | None, web_url: str
) -> str:
    lines = [
        "LinkedIn Easy Apply: you click Apply on LinkedIn; jobhunt never touches your account.",
        f"CV: {web_url}/applications/{application.id}",
        "Answers you may be asked:",
    ]
    if answers is None:
        lines.append("(no answers.yaml yet: run jobhunt setup)")
    else:
        lines += [
            f"Work authorisation: {answers.work_authorization}",
            f"Sponsorship needed outside the EU: {_yes_no(answers.needs_sponsorship_outside_eu)}",
            f"Notice period: {answers.notice_period}",
            f"Salary range: €{answers.salary_min_eur:,} to €{answers.salary_max_eur:,}",
            f"Relocation: {_yes_no(answers.relocation)}",
            f"Remote: {_yes_no(answers.remote)}",
        ]
    # _clean strips newlines too, so each line is cleaned on its own.
    return "\n".join(_clean(line) for line in lines)[:1200]
