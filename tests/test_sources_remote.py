from pathlib import Path

import httpx
import respx

from jobhunt.sources.remote_boards import (
    RemoteOkSource,
    RemotiveSource,
    WeWorkRemotelySource,
)

FIXTURES = Path(__file__).parent / "fixtures"


@respx.mock
def test_remotive_maps_fields():
    respx.get(url__startswith="https://remotive.com/api/remote-jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 7,
                        "title": "Backend Engineer",
                        "company_name": "Acme",
                        "url": "https://example.com/7",
                        "description": "<p>Build APIs</p>",
                        "candidate_required_location": "Europe",
                        "publication_date": "2026-09-01T10:00:00",
                    }
                ]
            },
        )
    )

    postings = list(RemotiveSource().fetch())
    assert len(postings) == 1
    p = postings[0]

    assert p.source == "remotive"
    assert p.company == "Acme"
    assert p.remote is True
    assert p.description == "Build APIs"


@respx.mock
def test_remoteok_skips_the_legal_metadata_element():
    respx.get("https://remoteok.com/api").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"legal": "API Terms of Service: please link back"},
                {
                    "id": "9",
                    "position": "Platform Engineer",
                    "company": "Acme",
                    "url": "https://example.com/9",
                    "description": "<p>Run things</p>",
                    "location": "Worldwide",
                    "date": "2026-09-01T10:00:00+00:00",
                },
            ],
        )
    )

    postings = list(RemoteOkSource().fetch())

    assert len(postings) == 1
    assert postings[0].title == "Platform Engineer"


@respx.mock
def test_weworkremotely_parses_rss_and_splits_company_from_title():
    rss = (FIXTURES / "wwr.rss").read_text(encoding="utf-8")
    respx.get(url__startswith="https://weworkremotely.com").mock(
        return_value=httpx.Response(200, text=rss, headers={"content-type": "application/rss+xml"})
    )

    postings = list(WeWorkRemotelySource().fetch())
    assert len(postings) == 1
    p = postings[0]

    assert p.source == "weworkremotely"
    assert p.company == "Acme"
    assert p.title == "Senior Backend Engineer"
    assert p.remote is True
    assert "Build APIs in Python" in p.description


@respx.mock
def test_remote_source_failure_yields_nothing_and_does_not_raise():
    respx.get(url__startswith="https://remotive.com").mock(return_value=httpx.Response(503))

    assert list(RemotiveSource().fetch()) == []


@respx.mock
def test_wwr_prefix_that_is_not_a_company_falls_back_to_unknown():
    rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
<title>Remote: Backend Engineer</title>
<link>https://weworkremotely.com/jobs/2</link>
<description>Build things</description>
</item></channel></rss>"""
    respx.get(url__startswith="https://weworkremotely.com").mock(
        return_value=httpx.Response(200, text=rss)
    )

    p = next(iter(WeWorkRemotelySource().fetch()))

    assert p.company == "unknown"
    assert p.title == "Backend Engineer"
