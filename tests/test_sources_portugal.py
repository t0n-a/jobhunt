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
