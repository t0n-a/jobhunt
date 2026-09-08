import httpx
import respx

from jobhunt.sources.ats import AshbySource, GreenhouseSource, LeverSource, strip_html


def test_strip_html_removes_tags_and_unescapes():
    assert strip_html("<p>Build&nbsp;APIs &amp; ship</p>") == "Build APIs & ship"


@respx.mock
def test_greenhouse_maps_fields():
    route = respx.get(
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        params={"content": "true"},
    ).mock(
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
    # content=true is what makes Greenhouse return the description at all.
    assert route.called
    assert "content=true" in str(route.calls[0].request.url)


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
    route = respx.get("https://api.lever.co/v0/postings/acme", params={"mode": "json"}).mock(
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
    assert "mode=json" in str(route.calls[0].request.url)


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


@respx.mock
def test_one_malformed_item_does_not_lose_the_rest_of_the_board():
    """Spec §9: source isolation. A posting missing a required field must be
    skipped, not abort the board — this exercises the per-item try/except, which
    the board-level failure test does not reach."""
    respx.get(url__startswith="https://boards-api.greenhouse.io").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {"absolute_url": "u", "content": "d"},  # no id, no title
                    {
                        "id": 2,
                        "title": "Survivor",
                        "absolute_url": "u2",
                        "content": "d2",
                        "location": {"name": "Porto"},
                    },
                ]
            },
        )
    )

    postings = list(GreenhouseSource(["acme"]).fetch())

    assert [p.title for p in postings] == ["Survivor"]


@respx.mock
def test_remote_detection_rejects_near_miss_locations():
    respx.get(url__startswith="https://api.ashbyhq.com").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "a",
                        "title": "A",
                        "jobUrl": "u",
                        "descriptionPlain": "d",
                        "location": "Remote-friendly (Porto office, on-site required)",
                        "isRemote": False,
                    },
                    {
                        "id": "b",
                        "title": "B",
                        "jobUrl": "u",
                        "descriptionPlain": "d",
                        "location": "Remoteness, Alaska",
                        "isRemote": False,
                    },
                    {
                        "id": "c",
                        "title": "C",
                        "jobUrl": "u",
                        "descriptionPlain": "d",
                        "location": "Remote (EU)",
                        "isRemote": False,
                    },
                ]
            },
        )
    )

    remote_by_id = {p.external_id: p.remote for p in AshbySource(["acme"]).fetch()}

    assert remote_by_id == {"a": False, "b": False, "c": True}


def test_epoch_parser_rejects_a_seconds_based_value():
    """A seconds epoch divided by 1000 would silently yield a 1970 date."""
    from jobhunt.sources.ats import _parse_epoch_ms

    assert _parse_epoch_ms(1788000000) is None
    assert _parse_epoch_ms(1788000000000) is not None
