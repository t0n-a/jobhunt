import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from jobhunt.apply.browser import obstacles, run_form, scan_fields
from jobhunt.apply.plan import FormPlan, Planned

try:
    with sync_playwright() as _p:
        _p.chromium.launch().close()
except Exception:  # noqa: BLE001 - any launch failure means no browser here
    pytest.skip("chromium not available", allow_module_level=True)

FORMS = Path(__file__).parent / "fixtures" / "forms"


class _Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/limited":
            self.send_error(429)
        else:
            super().do_GET()

    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(FORMS)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _plan(tmp_path, extra=()):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4")
    return FormPlan(
        filled=[
            Planned("first_name", "Ada", "profile"),
            Planned("last_name", "Lovelace", "profile"),
            Planned("email", "ada@example.com", "profile"),
            Planned("resume", str(cv), "document"),
            Planned("sponsorship", "No", "answers"),
            *extra,
        ]
    )


def _run(base, tmp_path, page, **kw):
    kw.setdefault("submit", True)
    return run_form(
        f"{base}/{page}", kw.pop("plan", None) or _plan(tmp_path),
        headless=True, screenshot_dir=tmp_path / "shots", **kw,
    )


def test_simple_form_submits_and_confirms(base, tmp_path):
    r = _run(base, tmp_path, "simple.html")
    assert r.status == "submitted" and Path(r.screenshot).exists()


def test_ready_when_not_submitting(base, tmp_path):
    r = _run(base, tmp_path, "simple.html", submit=False)
    assert r.status == "ready"


def test_login_is_an_obstacle(base, tmp_path):
    r = _run(base, tmp_path, "login.html")
    assert r.status == "stopped" and r.reason == "obstacle: login"


def test_invisible_captcha_is_an_obstacle(base, tmp_path):
    r = _run(base, tmp_path, "captcha_invisible.html")
    assert r.status == "stopped" and "captcha" in r.reason


def test_captcha_widget_is_an_obstacle(base, tmp_path):
    r = _run(base, tmp_path, "captcha_widget.html")
    assert r.status == "stopped" and "captcha" in r.reason


def test_allow_obstacles_lets_assisted_mode_fill(base, tmp_path):
    r = _run(base, tmp_path, "captcha_invisible.html", allow_obstacles=True, submit=False)
    assert r.status == "ready"


def test_unrecognised_required_field_stops(base, tmp_path):
    r = _run(base, tmp_path, "unknown_required.html")
    assert (r.status, r.reason) == ("stopped", "unrecognised field: Favourite colour")


def test_no_confirmation_is_an_error_not_sent(base, tmp_path):
    r = _run(base, tmp_path, "no_confirm.html", timeout_ms=800)
    assert (r.status, r.reason) == ("error", "no confirmation")


def test_scan_reads_labels_required_and_radio_groups(base):
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(f"{base}/select_radio.html")
        fields = scan_fields(page)
        assert obstacles(page) == []
        b.close()
    assert len(fields) == 1
    f = fields[0]
    assert (f.label, f.kind, f.options, f.required) == ("Relocate?", "select", ["Yes", "No"], True)


def test_radio_group_fills(base, tmp_path):
    plan = FormPlan(filled=[Planned("relocate", "Yes", "answers")])
    assert _run(base, tmp_path, "select_radio.html", plan=plan).status == "submitted"


def test_http_error_is_error(base, tmp_path):
    r = _run(base, tmp_path, "limited")
    assert (r.status, r.reason) == ("error", "http 429")


def test_a_captcha_injected_after_load_stops_before_submit(base, tmp_path):
    r = _run(base, tmp_path, "late_captcha.html")
    assert r.status == "stopped" and "captcha" in r.reason
