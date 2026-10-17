import httpx
import respx

from jobhunt.jobs.liveness import check_liveness, classify

U = "https://boards.greenhouse.io/acme/jobs/123456"

def test_gone_is_expired_and_blocked_is_uncertain():
    assert classify(404, U, U, "") == "expired"
    assert classify(410, U, U, "") == "expired"
    assert classify(403, U, U, "") == "uncertain"
    assert classify(429, U, U, "") == "uncertain"
    assert classify(200, U, U, "<title>Just a moment...</title>") == "uncertain"

def test_redirect_that_drops_the_job_id_is_uncertain():
    assert classify(200, U, "https://boards.greenhouse.io/acme", "Jobs") == "uncertain"

def test_closed_banner_with_condition_guard():
    assert classify(200, U, U, "This job is no longer accepting applications.") == "expired"
    assert classify(200, U, U, "Applications will close when the position is filled. We are no longer open after March.") == "active"
    assert classify(200, U, U, "Apply now") == "active"

@respx.mock
def test_network_error_is_uncertain():
    respx.get(U).mock(side_effect=httpx.ConnectError("boom"))
    with httpx.Client() as c:
        assert check_liveness(U, c) == "uncertain"
