import pytest

from jobhunt.apply.ats import AtsTarget, detect_ats

UUID = "8f3c2a1e-4b5d-4e6f-9a7b-0c1d2e3f4a5b"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://boards.greenhouse.io/acme/jobs/4001",
            AtsTarget("greenhouse", "acme", "4001", "https://job-boards.greenhouse.io/acme/jobs/4001"),
        ),
        (
            "https://JOB-BOARDS.GREENHOUSE.IO/acme/jobs/4001/",
            AtsTarget("greenhouse", "acme", "4001", "https://job-boards.greenhouse.io/acme/jobs/4001"),
        ),
        (
            "https://job-boards.eu.greenhouse.io/acme/jobs/9",
            AtsTarget("greenhouse", "acme", "9", "https://job-boards.eu.greenhouse.io/acme/jobs/9"),
        ),
        (
            "https://boards.greenhouse.io/embed/job_app?for=acme&token=4001",
            AtsTarget(
                "greenhouse",
                "acme",
                "4001",
                "https://job-boards.greenhouse.io/acme/jobs/4001",
            ),
        ),
        (
            f"https://jobs.lever.co/acme/{UUID}",
            AtsTarget("lever", "acme", UUID, f"https://jobs.lever.co/acme/{UUID}/apply"),
        ),
        (
            f"https://JOBS.EU.LEVER.CO/acme/{UUID}/apply/",
            AtsTarget("lever", "acme", UUID, f"https://jobs.eu.lever.co/acme/{UUID}/apply"),
        ),
        (
            f"https://jobs.ashbyhq.com/acme/{UUID}/application",
            AtsTarget("ashby", "acme", UUID, f"https://jobs.ashbyhq.com/acme/{UUID}/application"),
        ),
        (
            f"https://jobs.ashbyhq.com/acme/{UUID}/",
            AtsTarget("ashby", "acme", UUID, f"https://jobs.ashbyhq.com/acme/{UUID}/application"),
        ),
    ],
)
def test_recognised_urls(url, expected):
    assert detect_ats(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.com/careers?gh_jid=123",
        "https://jobs.lever.co/acme",
        f"ftp://jobs.lever.co/acme/{UUID}",
        "",
    ],
)
def test_unrecognised_urls_are_none(url):
    assert detect_ats(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example\\@job-boards.greenhouse.io/acme/jobs/1",
        "https://user:pw@job-boards.greenhouse.io/acme/jobs/1",
        "https://job-boards.greenhouse.io/ac%2Fme/jobs/1",
        "https://job-boards.greenhouse.io/embed/job_app?for=../x&token=1",
    ],
)
def test_spoofed_or_odd_urls_are_refused(url):
    assert detect_ats(url) is None


def test_greenhouse_apply_url_is_rebuilt_from_checked_parts():
    target = detect_ats("https://job-boards.greenhouse.io/acme/jobs/12?gh_src=x#apply")
    assert target.apply_url == "https://job-boards.greenhouse.io/acme/jobs/12"
