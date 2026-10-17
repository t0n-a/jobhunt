"""Drive a real application form in Chromium: obstacles, scan, fill, submit."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from jobhunt.apply.form import FormField
from jobhunt.apply.plan import FormPlan
from jobhunt.jobs.liveness import CLOSED_PHRASES
from jobhunt.ranking.shortlist import _clean

# Why: invisible captchas still score the visitor as a bot, and the spec stops at a captcha.
CAPTCHA_MARKERS = (
    "recaptcha", "hcaptcha", "h-captcha", "turnstile", "challenges.cloudflare.com",
    "g-recaptcha-response", "h-captcha-response",
)  # fmt: skip
# Why: a sent application is recorded only when the site itself says it arrived.
CONFIRM_RE = re.compile(
    r"thank you for (applying|your application)|application (has been )?(submitted|received)"
    r"|we('ve| have) received your application",
    re.IGNORECASE,
)
_SUBMIT_TEXT_RE = re.compile(
    r"^\s*(submit( application)?|apply( now)?|send application)\s*$", re.IGNORECASE
)
_LOGIN_PATHS = ("/login", "/signin", "/sign-in")

_SCAN_JS = """() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const clean = s => (s || "").replace(/\\s+/g, " ").trim();
  const labelOf = e => {
    const byFor = e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`);
    if (byFor && clean(byFor.textContent)) return clean(byFor.textContent);
    if (e.getAttribute("aria-label")) return clean(e.getAttribute("aria-label"));
    const anc = e.closest("label");
    if (anc && clean(anc.textContent)) return clean(anc.textContent);
    return "";
  };
  const out = [], radios = {};
  for (const e of document.querySelectorAll("input, textarea, select")) {
    const name = e.getAttribute("name") || e.id, type = (e.getAttribute("type") || "text").toLowerCase();
    if (!name || !vis(e) || /captcha-response/i.test(name)) continue;
    if (["hidden", "submit", "button", "password", "search"].includes(type)) continue;
    const required = e.hasAttribute("required") || e.getAttribute("aria-required") === "true";
    if (type === "radio") {
      let g = radios[name];
      if (!g) {
        const lg = e.closest("fieldset")?.querySelector("legend");
        g = radios[name] = {name, label: clean(lg?.textContent) || name, kind: "select",
                            required: false, options: []};
        out.push(g);
      }
      g.required = g.required || required;
      g.options.push(labelOf(e) || e.value);
      continue;
    }
    if (e.getAttribute("role") === "combobox") {  // react-select: options exist only once opened
      out.push({name, label: labelOf(e) || name, kind: "select", required, options: []});
      continue;
    }
    let kind = e.tagName === "SELECT" ? "select" : e.tagName === "TEXTAREA" ? "textarea" : "text";
    if (type === "checkbox") kind = "checkbox";
    if (type === "file") kind = "file";
    if (e.tagName === "SELECT" && e.multiple) kind = "multiselect";
    const options = e.tagName === "SELECT"
      ? [...e.options].map(o => clean(o.textContent)).filter(Boolean) : [];
    out.push({name, label: labelOf(e) || name, kind, required, options});
  }
  return out;
}"""

_RADIO_JS = """(el, want) => {
  const clean = s => (s || "").replace(/\\s+/g, " ").trim();
  const l = (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`))
    || el.closest("label");
  return clean(l ? l.textContent : el.value) === want;
}"""


@dataclass
class FormRun:
    status: str  # submitted | ready | stopped | error
    reason: str = ""
    screenshot: str | None = None
    clicked: bool = False  # the submit button was pressed: the application may have gone out

    def __post_init__(self) -> None:
        # Reasons quote page labels and reach notes, the terminal and Telegram.
        self.reason = _clean(self.reason)


# Why: SPAs render the form after load; waiting for the network to settle is best-effort.
_SETTLE_MS = 10_000


def settle(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=_SETTLE_MS)
    except PlaywrightTimeout:
        pass


def _body(page) -> str:
    return page.inner_text("body")


def obstacles(page) -> list[str]:
    found = []
    path = urlparse(page.url).path.lower()
    if page.locator("input[type=password]:visible").count() or any(p in path for p in _LOGIN_PATHS):
        found.append("login")
    srcs = [f.url for f in page.frames]
    srcs += page.eval_on_selector_all("script[src], iframe[src]", "els => els.map(e => e.src)")
    haystack = page.content().lower() + " ".join(srcs).lower()
    if any(m in haystack for m in CAPTCHA_MARKERS):
        found.append("captcha")
    text = _body(page).casefold()
    if any(p in text for p in CLOSED_PHRASES):
        found.append("closed")
    return found


def scan_fields(page) -> list[FormField]:
    return [FormField(**d) for d in page.evaluate(_SCAN_JS)]


def _locate(page, key: str):
    q = key.replace("\\", "\\\\").replace('"', '\\"')
    by_name = page.locator(f'[name="{q}"]')
    return by_name if by_name.count() else page.locator(f'[id="{q}"]')


def _pick_option(page, first, label: str) -> None:
    first.click()
    first.fill(label)
    want = re.compile(rf"^\s*{re.escape(label.strip())}\s*$", re.IGNORECASE)
    page.locator("[role=option]").filter(has_text=want).first.click(timeout=3000)


def _fill_one(page, p) -> None:
    loc = _locate(page, p.name)
    first = loc.first
    if first.get_attribute("role") == "combobox":
        return _pick_option(page, first, p.value)
    tag = first.evaluate("e => e.tagName")
    typ = (first.get_attribute("type") or "").lower()
    if typ == "radio":
        for i in range(loc.count()):
            if loc.nth(i).evaluate(_RADIO_JS, p.value):
                loc.nth(i).check()
                return
        raise ValueError(p.value)
    if typ == "file":
        first.set_input_files(p.value)
    elif typ == "checkbox":
        first.check() if p.value else first.uncheck()
    elif tag == "SELECT":
        first.select_option(label=p.value)
    else:
        first.fill(p.value)


def fill(page, plan: FormPlan) -> list[str]:
    failed = []
    for p in plan.filled:
        try:
            _fill_one(page, p)
        except (PlaywrightError, ValueError):
            failed.append(p.name)
    return failed


def submit_control(page):
    for sel in ("button[type=submit]:visible", "input[type=submit]:visible"):
        if page.locator(sel).count():
            return page.locator(sel).first
    for sel in ("button", "[role=button]", "a"):
        loc = page.locator(sel).filter(has_text=_SUBMIT_TEXT_RE).locator("visible=true")
        for i in range(loc.count()):
            if _SUBMIT_TEXT_RE.match(loc.nth(i).inner_text()):
                return loc.nth(i)
    btn = page.locator("#btn-submit")
    return btn.first if btn.count() else None


def run_form(*args, **kwargs) -> FormRun:
    """Never raises: a failure is an error run, and `clicked` says whether it came after the click."""
    state: dict = {}
    try:
        return _run_form(*args, _state=state, **kwargs)
    except Exception as e:  # noqa: BLE001 - launch, scan, screenshot: all end the run, not the caller
        return FormRun("error", type(e).__name__, clicked=state.get("clicked", False))


def _run_form(
    apply_url: str,
    plan: FormPlan,
    *,
    headless: bool,
    submit: bool,
    screenshot_dir: Path,
    allow_obstacles: bool = False,
    wait_for_human: bool = False,
    timeout_ms: int = 30_000,
    _state: dict,
) -> FormRun:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            page = browser.new_page()

            def done(status: str, reason: str = "", shot: bool = True) -> FormRun:
                path = None
                if shot:
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    path = str(screenshot_dir / f"{int(time.time())}-{status}.png")
                    page.screenshot(path=path)
                return FormRun(status, reason, path, clicked=_state.get("clicked", False))

            try:
                resp = page.goto(apply_url, timeout=timeout_ms)
            except Exception as e:  # noqa: BLE001 - any navigation failure is an error run
                return FormRun("error", type(e).__name__)
            if resp is not None and (resp.status in (403, 429) or resp.status >= 500):
                return FormRun("error", f"http {resp.status}")
            settle(page)
            # The ATS host was checked, not wherever a redirect led (e.g. the employer's own site).
            want = urlparse(apply_url).hostname
            if urlparse(page.url).hostname != want:
                return done("stopped", f"redirected to {urlparse(page.url).hostname}")
            found = obstacles(page)
            if found and not allow_obstacles:
                return done("stopped", "obstacle: " + ", ".join(found))
            have = {p.name for p in plan.filled}
            fields = scan_fields(page)
            if not fields:
                return done("stopped", "no form fields found")
            # Assisted mode fills what is known; the human answers the rest.
            lenient = allow_obstacles and not submit
            for f in fields:
                if f.required and f.name not in have and not lenient:
                    return done("stopped", f"unrecognised field: {f.label}")
            failed = fill(page, plan)
            if failed and not lenient:
                return done("stopped", "could not fill: " + ", ".join(failed))
            left = "left for you: " + ", ".join(failed) if failed else ""
            btn = submit_control(page)
            if btn is None:
                return done("stopped", "no submit button")
            if not submit:
                if wait_for_human:
                    try:
                        page.wait_for_event("close", timeout=0)
                    except PlaywrightError:  # the candidate closed the whole window, not the tab
                        pass
                return FormRun("ready", left)
            # Captcha scripts can arrive after load (on focus, on fill): look again before clicking.
            late = obstacles(page)
            if late and not allow_obstacles:
                return done("stopped", "obstacle: " + ", ".join(late))
            if urlparse(page.url).hostname != want:
                return done("stopped", f"redirected to {urlparse(page.url).hostname}")
            # Boilerplate ("once we have received your application...") may already be on the page.
            before = len(CONFIRM_RE.findall(_body(page)))
            _state["clicked"] = True
            btn.click()
            try:
                page.wait_for_function(
                    "([re, n]) => (document.body.innerText.match("
                    "new RegExp(re.source, re.flags + 'g')) || []).length > n",
                    arg=[{"source": CONFIRM_RE.pattern, "flags": "i"}, before],
                    timeout=timeout_ms,
                )
            except PlaywrightTimeout:
                return done("error", "no confirmation")
            settle(page)  # let the POST finish before the browser closes
            return done("submitted")
        finally:
            browser.close()
