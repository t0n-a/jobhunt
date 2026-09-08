import httpx
import respx

from jobhunt.sources.hackernews import HackerNewsSource

SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
ITEM = "https://hacker-news.firebaseio.com/v0/item"


@respx.mock
def test_hackernews_reads_the_latest_thread_and_filters_by_keyword():
    respx.get(url__startswith=SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "objectID": "49522897",
                        "title": "Ask HN: Who is hiring? (September 2026)",
                    }
                ]
            },
        )
    )
    respx.get(f"{ITEM}/49522897.json").mock(return_value=httpx.Response(200, json={"kids": [1, 2]}))
    respx.get(f"{ITEM}/1.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 1,
                "by": "acme",
                "text": "Acme | Porto, Portugal | Backend Engineer | REMOTE",
            },
        )
    )
    respx.get(f"{ITEM}/2.json").mock(
        return_value=httpx.Response(
            200, json={"id": 2, "by": "other", "text": "Nowhere Inc | Tokyo | Sales"}
        )
    )

    postings = list(HackerNewsSource(["porto", "remote"]).fetch())

    assert len(postings) == 1
    assert postings[0].external_id == "1"
    assert postings[0].source == "hackernews"
    assert postings[0].remote is True


@respx.mock
def test_hackernews_skips_deleted_comments():
    respx.get(url__startswith=SEARCH).mock(
        return_value=httpx.Response(200, json={"hits": [{"objectID": "9", "title": "t"}]})
    )
    respx.get(f"{ITEM}/9.json").mock(return_value=httpx.Response(200, json={"kids": [3]}))
    respx.get(f"{ITEM}/3.json").mock(
        return_value=httpx.Response(200, json={"id": 3, "deleted": True})
    )

    assert list(HackerNewsSource(["porto"]).fetch()) == []


@respx.mock
def test_hackernews_no_thread_found_yields_nothing():
    respx.get(url__startswith=SEARCH).mock(return_value=httpx.Response(200, json={"hits": []}))

    assert list(HackerNewsSource(["porto"]).fetch()) == []


@respx.mock
def test_hackernews_comment_without_pipes_still_yields_a_bounded_posting():
    """HN comments are free prose; many do not follow "Company | Location | Role".
    The fallback must stay bounded rather than emitting a whole paragraph."""
    respx.get(url__startswith=SEARCH).mock(
        return_value=httpx.Response(
            200, json={"hits": [{"objectID": "7", "title": "Ask HN: Who is hiring?"}]}
        )
    )
    respx.get(f"{ITEM}/7.json").mock(return_value=httpx.Response(200, json={"kids": [11]}))
    prose = "We are a small team in Porto looking for someone to help us " * 8
    respx.get(f"{ITEM}/11.json").mock(
        return_value=httpx.Response(200, json={"id": 11, "by": "x", "text": prose})
    )

    postings = list(HackerNewsSource(["porto"]).fetch())

    assert len(postings) == 1
    assert len(postings[0].title) <= 200
    assert len(postings[0].company) <= 200
    assert postings[0].description.startswith("We are a small team in Porto")
