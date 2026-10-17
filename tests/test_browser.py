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
        elif self.path == "/redirect":
            self.send_response(302)
            # localhost vs 127.0.0.1 is a different host
            self.send_header("Location", f"http://localhost:{self.server.server_port}/simple.html")
            self.end_headers()
        else:
            super().do_GET()

    def do_POST(self):
        body = b"<html><body><h1>Thank you for applying</h1></body></html>"
        self.send_response(200 if self.path == "/thanks" else 404)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


def test_preexisting_confirmation_text_is_not_a_confirmation(base, tmp_path):
    r = _run(base, tmp_path, "confirm_boilerplate.html", timeout_ms=800)
    assert (r.status, r.reason) == ("error", "no confirmation")


def test_real_post_navigation_submits(base, tmp_path):
    assert _run(base, tmp_path, "post_submit.html").status == "submitted"


def test_late_rendered_fields_are_seen_and_filled(base, tmp_path):
    assert _run(base, tmp_path, "late_render.html").status == "submitted"


def test_a_page_with_no_fields_stops(base, tmp_path):
    r = _run(base, tmp_path, "empty.html")
    assert (r.status, r.reason) == ("stopped", "no form fields found")


def test_redirect_to_another_host_stops(base, tmp_path):
    r = _run(base, tmp_path, "redirect")
    assert r.status == "stopped" and r.reason.startswith("redirected to localhost")


def test_assisted_mode_leaves_unknown_required_fields_to_the_human(base, tmp_path):
    r = _run(base, tmp_path, "unknown_required.html", submit=False, allow_obstacles=True)
    assert r.status == "ready"


def test_reasons_are_cleaned_of_control_characters():
    from jobhunt.apply.browser import FormRun

    assert FormRun("stopped", "unrecognised field: A\x1b[31mB\u202e").reason == "unrecognised field: A[31mB"


def test_error_before_the_click_is_not_clicked(base, tmp_path, monkeypatch):
    import jobhunt.apply.browser as b

    def boom(page):
        raise RuntimeError("x")

    monkeypatch.setattr(b, "scan_fields", boom)
    r = _run(base, tmp_path, "simple.html")
    assert (r.status, r.reason, r.clicked) == ("error", "RuntimeError", False)


def test_error_after_the_click_is_clicked(base, tmp_path, monkeypatch):
    import jobhunt.apply.browser as b

    real, calls = b.settle, []

    def settle_then_boom(page):
        calls.append(1)
        if len(calls) > 1:  # the first settle is after goto, the second after the click
            raise RuntimeError("x")
        real(page)

    monkeypatch.setattr(b, "settle", settle_then_boom)
    r = _run(base, tmp_path, "simple.html")
    assert (r.status, r.reason, r.clicked) == ("error", "RuntimeError", True)


def test_no_confirmation_counts_as_clicked(base, tmp_path):
    assert _run(base, tmp_path, "no_confirm.html", timeout_ms=800).clicked is True
    assert _run(base, tmp_path, "simple.html").clicked is True
    assert _run(base, tmp_path, "simple.html", submit=False).clicked is False
