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


def test_malformed_urls_are_uncertain():
    with httpx.Client() as c:
        assert check_liveness("http://[bad", c) == "uncertain"
        assert check_liveness("http://exa mple.com", c) == "uncertain"


def test_non_http_and_private_targets_are_never_probed():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="ok")

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        for url in ("file:///etc/passwd", "http://127.0.0.1/x", "http://169.254.169.254/",
                    "http://localhost/x", "http://10.0.0.5/x", "http://[::1]/x"):
            assert check_liveness(url, c) == "uncertain"
        assert calls == []
        assert check_liveness(U, c) == "active"
        assert len(calls) == 1


def test_loopback_aliases_and_non_global_ips_are_never_probed():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="ok")

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        for url in ("http://localhost./x", "http://app.localhost/x", "http://0x7f000001/x",
                    "http://2130706433/x", "http://0177.0.0.1/x", "http://100.64.0.1/x"):
            assert check_liveness(url, c) == "uncertain"
        assert calls == []
