import httpx
import respx

from jobhunt.sources.ats import AshbySource, GreenhouseSource, LeverSource, strip_html


def test_strip_html_removes_tags_and_unescapes():
    assert strip_html("<p>Build&nbsp;APIs &amp; ship</p>") == "Build APIs & ship"


@respx.mock
def test_greenhouse_maps_fields():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 42,
                        "title": "Backend Engineer",
                        "absolute_url": "https://example.com/42",
                        "content": "<p>Build APIs</p>",
                        "location": {"name": "Porto, Portugal"},
                        "updated_at": "2026-09-01T10:00:00Z",
                    }
                ]
            },
        )
    )

    postings = list(GreenhouseSource(["acme"]).fetch())

    assert len(postings) == 1
    p = postings[0]
    assert p.source == "greenhouse"
    assert p.external_id == "42"
    assert p.company == "acme"
    assert p.title == "Backend Engineer"
    assert p.location == "Porto, Portugal"
    assert p.description == "Build APIs"
    assert p.content_hash


@respx.mock
def test_greenhouse_one_failing_board_does_not_lose_the_others():
    respx.get("https://boards-api.greenhouse.io/v1/boards/bad/jobs").mock(
        return_value=httpx.Response(500)
    )
    respx.get("https://boards-api.greenhouse.io/v1/boards/good/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 1,
                        "title": "T",
                        "absolute_url": "u",
                        "content": "d",
                        "location": {"name": "Remote"},
                    }
                ]
            },
        )
    )

    postings = list(GreenhouseSource(["bad", "good"]).fetch())

    assert [p.external_id for p in postings] == ["1"]


@respx.mock
def test_lever_maps_fields_and_detects_remote():
    respx.get("https://api.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "abc-123",
                    "text": "Platform Engineer",
                    "hostedUrl": "https://example.com/abc",
                    "descriptionPlain": "Run the platform",
                    "categories": {"location": "Remote (EU)"},
                    "createdAt": 1788000000000,
                }
            ],
        )
    )

    postings = list(LeverSource(["acme"]).fetch())
    assert len(postings) == 1
    p = postings[0]

    assert p.source == "lever"
    assert p.external_id == "abc-123"
    assert p.remote is True
    assert p.description == "Run the platform"


@respx.mock
def test_ashby_maps_fields():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "u-1",
                        "title": "Data Engineer",
                        "jobUrl": "https://example.com/u-1",
                        "descriptionPlain": "Pipelines",
                        "location": "Lisbon",
                        "isRemote": False,
                    }
                ]
            },
        )
    )

    postings = list(AshbySource(["acme"]).fetch())
    assert len(postings) == 1
    p = postings[0]

    assert p.source == "ashby"
    assert p.external_id == "u-1"
    assert p.title == "Data Engineer"
    assert p.remote is False


@respx.mock
def test_ats_source_with_no_boards_makes_no_requests():
    route = respx.get(url__startswith="https://boards-api.greenhouse.io")

    assert list(GreenhouseSource([]).fetch()) == []
    assert route.call_count == 0
