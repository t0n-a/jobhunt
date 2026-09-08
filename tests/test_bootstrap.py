import httpx
import respx

from jobhunt.sources.bootstrap import discover_boards


@respx.mock
def test_discover_boards_keeps_only_slugs_with_jobs():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"id": 1}]})
    )
    respx.get("https://boards-api.greenhouse.io/v1/boards/ghost/jobs").mock(
        return_value=httpx.Response(404)
    )
    respx.get(url__startswith="https://api.lever.co").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(url__startswith="https://api.ashbyhq.com").mock(return_value=httpx.Response(404))

    found = discover_boards(["acme", "ghost"])

    assert found["greenhouse"] == ["acme"]
    assert found["lever"] == []
    assert found["ashby"] == []


@respx.mock
def test_discover_boards_survives_a_transport_error():
    respx.get(url__startswith="https://boards-api.greenhouse.io").mock(
        side_effect=httpx.ConnectError("down")
    )
    respx.get(url__startswith="https://api.lever.co").mock(
        return_value=httpx.Response(200, json=[{"id": "x"}])
    )
    respx.get(url__startswith="https://api.ashbyhq.com").mock(return_value=httpx.Response(404))

    found = discover_boards(["acme"])

    assert found["greenhouse"] == []
    assert found["lever"] == ["acme"]


@respx.mock
def test_a_board_that_exists_but_is_empty_is_not_kept():
    """An empty board is indistinguishable from a wrong slug for our purposes,
    and keeping it would put a dead entry in the user's config."""
    respx.get(url__startswith="https://boards-api.greenhouse.io").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    respx.get(url__startswith="https://api.lever.co").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(url__startswith="https://api.ashbyhq.com").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )

    assert discover_boards(["acme"]) == {"greenhouse": [], "lever": [], "ashby": []}


@respx.mock
def test_malformed_json_is_treated_as_no_board():
    respx.get(url__startswith="https://boards-api.greenhouse.io").mock(
        return_value=httpx.Response(200, text="not json")
    )
    respx.get(url__startswith="https://api.lever.co").mock(return_value=httpx.Response(404))
    respx.get(url__startswith="https://api.ashbyhq.com").mock(return_value=httpx.Response(404))

    assert discover_boards(["acme"])["greenhouse"] == []


@respx.mock
def test_no_slugs_makes_no_requests():
    route = respx.get(url__startswith="https://boards-api.greenhouse.io")

    assert discover_boards([]) == {"greenhouse": [], "lever": [], "ashby": []}
    assert route.call_count == 0
