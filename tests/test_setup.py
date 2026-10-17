import os
import stat
import subprocess
import sys

import httpx
import pytest
import respx
import yaml
from dotenv import dotenv_values

from jobhunt.answers import load_answers
from jobhunt.config import load_config
from jobhunt.llm import FakeProvider
from jobhunt.setup import Prompter, run_setup, setup_evidence

KEY = "AIza-SECRET-KEY"
TOKEN = "123:SECRET-TOKEN"
CONFIG = ["Hugo", "hugo@example.com", "Lisbon", ""]
ANSWERS = ["EU citizen", "n", "1 month", "40000", "60000", "n", "y", "", "", "", "", "y"]


BROWSER = [sys.executable, "-m", "playwright", "install", "chromium"]


class Browser:
    """Stands in for subprocess.run: records the install command and never runs it."""

    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self.error:
            raise self.error


class Script:
    def __init__(self, asks=(), secrets=()):
        self.asks, self.secrets = list(asks), list(secrets)
        self.said, self.calls = [], 0
        self.prompter = Prompter(self._ask, self._secret, self.said.append)

    def _ask(self, _prompt):
        self.calls += 1
        return self.asks.pop(0)

    def _secret(self, _prompt):
        self.calls += 1
        return self.secrets.pop(0)


CODE = "123456"


def updates(chat_id=4242):
    message = {"chat": {"id": chat_id, "type": "private"}, "text": CODE}
    return {"ok": True, "result": [{"update_id": 1, "message": message}]}


@pytest.fixture(autouse=True)
def fixed_code(monkeypatch):
    monkeypatch.setattr("jobhunt.setup._new_code", lambda: CODE)


def run(tmp_path, asks, secrets, result=None):
    s = Script(asks, secrets)
    with respx.mock:
        respx.post(f"https://api.telegram.org/bot{TOKEN}/getUpdates").respond(
            200, json=result or updates()
        )
        with httpx.Client() as http:
            run_setup(tmp_path, s.prompter, http, llm=FakeProvider([]), install=lambda r: "ok", run=Browser())
    return s


FULL = CONFIG + ["", *ANSWERS, "", "n", "n"]  # config, chat-id Enter, answers, no CV, no browser, no timers


def test_fresh_config_is_written_and_loads(tmp_path):
    run(tmp_path, FULL, [KEY, TOKEN])
    assert load_config(tmp_path / "config.yaml").llm.provider == "openai_compat"


def test_rerun_keeps_existing_files(tmp_path):
    run(tmp_path, FULL, [KEY, TOKEN])
    before = {n: (tmp_path / n).read_bytes() for n in ("config.yaml", "answers.yaml", ".env")}
    s = run(tmp_path, ["", "n", "n"], [])
    assert s.calls == 3  # only the CV, browser and timers questions
    assert before == {n: (tmp_path / n).read_bytes() for n in before}


def test_keys_written_with_private_mode(tmp_path):
    run(tmp_path, FULL, [KEY, TOKEN])
    assert stat.S_IMODE(os.stat(tmp_path / ".env").st_mode) == 0o600
    env = dotenv_values(tmp_path / ".env")
    assert env["OPENAI_API_KEY"] == KEY
    assert env["OPENAI_BASE_URL"].startswith("https://generativelanguage.googleapis.com")
    assert env["TELEGRAM_BOT_TOKEN"] == TOKEN


def test_training_warning_is_shown_before_the_key_prompt(tmp_path):
    s = Script(FULL, [KEY, TOKEN])
    order = []
    s.prompter.say = lambda m: order.append(("say", m))
    orig = s._secret
    s.prompter.secret = lambda p: (order.append(("secret", p)), orig(p))[1]
    with respx.mock:
        respx.post(f"https://api.telegram.org/bot{TOKEN}/getUpdates").respond(200, json=updates())
        with httpx.Client() as http:
            run_setup(tmp_path, s.prompter, http, llm=FakeProvider([]), install=str, run=Browser())
    assert order[0][0] == "say" and "may use your prompts" in order[0][1]
    assert order[1] == ("secret", "AI Studio key: ")


def test_chat_id_discovered(tmp_path):
    run(tmp_path, FULL, [KEY, TOKEN])
    assert dotenv_values(tmp_path / ".env")["TELEGRAM_CHAT_ID"] == "4242"


def test_no_message_leaves_chat_id_unset(tmp_path):
    s = run(tmp_path, FULL, [KEY, TOKEN], result={"ok": True, "result": []})
    assert not dotenv_values(tmp_path / ".env").get("TELEGRAM_CHAT_ID")
    assert any("code was not found" in m for m in s.said)


def test_answers_written(tmp_path):
    run(tmp_path, FULL, [KEY, TOKEN])
    a = load_answers(tmp_path / "answers.yaml")
    assert a.work_authorization == "EU citizen" and a.salary_max_eur == 60000
    assert a.relocation is False and a.remote is True
    assert a.gender == a.ethnicity == a.disability == a.veteran == "Prefer not to say"


def test_consent_is_asked_and_recorded(tmp_path):
    answers = [*ANSWERS[:-1], "n"]
    run(tmp_path, CONFIG + ["", *answers, "", "n", "n"], [KEY, TOKEN])
    assert load_answers(tmp_path / "answers.yaml").data_processing_consent is False


def test_bad_int_is_reasked(tmp_path):
    asks = CONFIG + [""] + ["EU", "n", "1 month", "lots", "40000", "60000", "n", "y", "", "", "", "", "y", "", "n", "n"]
    s = run(tmp_path, asks, [KEY, TOKEN])
    assert load_answers(tmp_path / "answers.yaml").salary_min_eur == 40000
    assert any("whole number" in m for m in s.said)


def test_secrets_never_echoed(tmp_path):
    s = run(tmp_path, FULL, [KEY, TOKEN])
    assert not any(KEY in m or TOKEN in m for m in s.said)
    assert yaml.safe_load((tmp_path / "config.yaml").read_text())["profile"]["name"] == "Hugo"


def test_max_salary_below_min_is_reasked(tmp_path):
    asks = CONFIG + [""] + ["EU", "n", "1 month", "40000", "30000", "60000", "n", "y", "", "", "", "", "y", "", "n", "n"]
    s = run(tmp_path, asks, [KEY, TOKEN])
    assert load_answers(tmp_path / "answers.yaml").salary_max_eur == 60000
    assert any("at least 40000" in m for m in s.said)


def test_a_stranger_without_the_code_is_not_bound(tmp_path):
    stranger = {"ok": True, "result": [
        {"update_id": 2, "message": {"chat": {"id": 666, "type": "private"}, "text": "hi"}},
        {"update_id": 1, "message": {"chat": {"id": 4242, "type": "private"}, "text": CODE}},
    ]}
    run(tmp_path, FULL, [KEY, TOKEN], result=stranger)
    assert dotenv_values(tmp_path / ".env")["TELEGRAM_CHAT_ID"] == "4242"


# --- evidence + timers ---
CV = "tests/fixtures/sample_cv.txt"


def atoms_llm(n=3):
    return FakeProvider([{"atoms": [
        {"kind": "project", "title": f"T{i}", "description": f"D{i}", "skills": ["Python"]}
        for i in range(n)]}])


def evidence(tmp_path, asks, llm):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(
        {"profile": {"name": "H", "email": "h@e.com", "location": "L"},
         "llm": {"provider": "openai_compat", "model": "m"}, "data_dir": "./data"}))
    s = Script(asks)
    return s, setup_evidence(tmp_path, s.prompter, llm)


def atoms(tmp_path):
    from sqlmodel import Session

    from jobhunt.db import create_engine_for
    from jobhunt.profile.store import AtomStore
    with Session(create_engine_for(tmp_path / "data" / "jobhunt.db")) as ses:
        return AtomStore(ses).list()


def test_cv_import_and_confirm(tmp_path):
    _, n = evidence(tmp_path, [CV, "y", "n", "s"], atoms_llm())
    got = atoms(tmp_path)
    assert n == 1 and len(got) == 2
    assert sorted(a.confirmed for a in got) == [False, True]


def test_quit_stops_confirming(tmp_path):
    s, n = evidence(tmp_path, [CV, "y", "q"], atoms_llm())
    assert n == 1 and not s.asks and len(atoms(tmp_path)) == 3


def test_existing_confirmed_atoms_skip_the_step(tmp_path):
    evidence(tmp_path, [CV, "y", "q"], atoms_llm())
    s, n = evidence(tmp_path, [], atoms_llm())
    assert n == 0 and s.calls == 0 and any("1 confirmed facts" in m for m in s.said)


def test_bad_path_is_reasked_then_skipped(tmp_path):
    s, n = evidence(tmp_path, ["/nope.txt", "/nope.pdf"], atoms_llm())
    assert n == 0 and s.calls == 2 and atoms(tmp_path) == []


def test_import_failure_does_not_abort_setup(tmp_path):
    s = Script(CONFIG + ["", *ANSWERS, CV, "n", "n"], [KEY, TOKEN])
    with respx.mock:
        respx.post(f"https://api.telegram.org/bot{TOKEN}/getUpdates").respond(200, json=updates())
        with httpx.Client() as http:
            run_setup(tmp_path, s.prompter, http, llm=FakeProvider([]), install=str, run=Browser())
    assert any("CV import failed" in m for m in s.said) and not s.asks


def test_timers_installed_only_on_yes(tmp_path):
    calls = []
    for answer in ("n", "y"):
        s = Script(["", "n", answer], [])
        (tmp_path / "config.yaml").write_text(yaml.safe_dump(
            {"profile": {"name": "H", "email": "h@e.com", "location": "L"},
             "llm": {"provider": "openai_compat", "model": "m"}}))
        (tmp_path / "answers.yaml").write_text("x")
        (tmp_path / ".env").write_text("OPENAI_API_KEY=k\nTELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=1\n")
        with httpx.Client() as http:
            run_setup(tmp_path, s.prompter, http, llm=FakeProvider([]),
                      install=lambda r: calls.append(r) or "ok",
                      run=Browser())
    assert calls == [tmp_path]


def ready(tmp_path):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(
        {"profile": {"name": "H", "email": "h@e.com", "location": "L"},
         "llm": {"provider": "openai_compat", "model": "m"}}))
    (tmp_path / "answers.yaml").write_text("x")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=k\nTELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=1\n")


BROWSER_Q = "Install the browser jobhunt uses to fill application forms (about 150 MB)? [y/N] "


@pytest.mark.parametrize("answer, expected", [("n", []), ("y", [BROWSER])])
def test_browser_install_only_on_yes(tmp_path, answer, expected):
    ready(tmp_path)
    s = Script(["", answer, "n"], [])
    asked = []
    prompter = Prompter(lambda q: (asked.append(q), s._ask(q))[1], s._secret, s.said.append)
    browser = Browser()
    with httpx.Client() as http:
        run_setup(tmp_path, prompter, http, llm=FakeProvider([]), install=str, run=browser)
    assert browser.calls == expected
    assert asked[-2:] == [BROWSER_Q, "Install the daily and 5-minute timers now? [y/N] "]


@pytest.mark.parametrize("exc", [subprocess.CalledProcessError(1, "playwright"), OSError("no uv")])
def test_browser_install_failure_is_reported(tmp_path, exc):
    ready(tmp_path)
    s = Script(["", "y", "n"], [])
    with httpx.Client() as http:
        run_setup(tmp_path, s.prompter, http, llm=FakeProvider([]), install=str,
                  run=Browser(error=exc))
    assert any(m.startswith("Browser install failed: ")
               and m.endswith("Run: uv run playwright install chromium") for m in s.said)
    assert not s.asks  # the timers question still follows
