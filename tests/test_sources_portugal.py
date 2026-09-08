import httpx
import respx

from jobhunt.sources.portugal import ITJobsSource, LandingJobsSource


@respx.mock
def test_landingjobs_maps_nested_location_and_tags():
    respx.get(url__startswith="https://landing.jobs/api/v1/jobs").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 19066,
                    "title": "Backend Developer",
                    "url": "https://landing.jobs/jobs/19066",
                    "role_description": "<p>Build services</p>",
                    "main_requirements": "<p>Python</p>",
                    "locations": [{"city": "Porto", "country_code": "PT"}],
                    "remote": False,
                    "tags": ["Python", "SQL"],
                    "published_at": "2026-09-01T10:00:00Z",
                }
            ],
        )
    )

    postings = list(LandingJobsSource().fetch())
    assert len(postings) == 1
    p = postings[0]

    assert p.source == "landingjobs"
    assert p.external_id == "19066"
    assert p.location == "Porto, PT"
    assert p.remote is False
    assert "Build services" in p.description
    assert "Python" in p.description


@respx.mock
def test_landingjobs_company_falls_back_when_absent():
    respx.get(url__startswith="https://landing.jobs/api/v1/jobs").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "title": "T",
                    "url": "u",
                    "role_description": "d",
                    "locations": [],
                    "remote": True,
                    "tags": [],
                }
            ],
        )
    )

    postings = list(LandingJobsSource().fetch())
    assert len(postings) == 1
    p = postings[0]

    assert p.company == "unknown"
    assert p.location is None


def test_itjobs_without_a_key_yields_nothing_and_makes_no_request():
    with respx.mock:
        route = respx.get(url__startswith="https://api.itjobs.pt")
        assert list(ITJobsSource(None).fetch()) == []
        assert route.call_count == 0


@respx.mock
def test_itjobs_with_an_invalid_key_yields_nothing_without_raising():
    respx.get(url__startswith="https://api.itjobs.pt").mock(
        return_value=httpx.Response(200, json={"error": {"message": "Invalid API Key"}})
    )

    assert list(ITJobsSource("bogus").fetch()) == []


@respx.mock
def test_itjobs_maps_results():
    respx.get(url__startswith="https://api.itjobs.pt").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 55,
                        "title": "Programador Backend",
                        "body": "<p>APIs</p>",
                        "company": {"name": "Acme Lda"},
                        "locations": [{"name": "Porto"}],
                        "publishedAt": "2026-09-01 10:00:00",
                    }
                ]
            },
        )
    )

    postings = list(ITJobsSource("realkey").fetch())
    assert len(postings) == 1
    p = postings[0]

    assert p.company == "Acme Lda"
    assert p.location == "Porto"


@respx.mock
def test_itjobs_sends_the_api_key_and_limit():
    """Nothing would fail if the key param were silently dropped or renamed,
    so assert the request shape rather than only the mapped response."""
    route = respx.get(url__startswith="https://api.itjobs.pt").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    list(ITJobsSource("secret-key").fetch())

    params = route.calls[0].request.url.params
    assert params["api_key"] == "secret-key"
    assert params["limit"] == "200"


@respx.mock
def test_itjobs_scalar_payload_does_not_raise():
    """A scalar JSON body would make `"results" not in payload` raise TypeError,
    which the per-item guards do not cover — it would abort the whole scan."""
    respx.get(url__startswith="https://api.itjobs.pt").mock(
        return_value=httpx.Response(200, json=42)
    )

    assert list(ITJobsSource("k").fetch()) == []


@respx.mock
def test_itjobs_non_list_results_does_not_iterate_characters():
    """A string `results` would iterate per character and log one warning each."""
    respx.get(url__startswith="https://api.itjobs.pt").mock(
        return_value=httpx.Response(200, json={"results": "oops"})
    )

    assert list(ITJobsSource("k").fetch()) == []


@respx.mock
def test_landingjobs_missing_description_fields_do_not_produce_leading_space():
    respx.get(url__startswith="https://landing.jobs/api/v1/jobs").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 5,
                    "title": "T",
                    "url": "u",
                    "locations": [],
                    "remote": True,
                    "tags": ["Python"],
                }
            ],
        )
    )

    p = next(iter(LandingJobsSource().fetch()))

    assert p.description == "Skills: Python."
    assert not p.description.startswith(" ")
