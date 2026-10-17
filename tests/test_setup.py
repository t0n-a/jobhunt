import os
import stat

import httpx
import respx
import yaml
from dotenv import dotenv_values

from jobhunt.answers import load_answers
from jobhunt.config import load_config
from jobhunt.setup import Prompter, run_setup

KEY = "AIza-SECRET-KEY"
TOKEN = "123:SECRET-TOKEN"
CONFIG = ["Hugo", "hugo@example.com", "Lisbon", ""]
ANSWERS = ["EU citizen", "n", "1 month", "40000", "60000", "n", "y", "", "", "", ""]


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


def updates(chat_id=4242):
    return {"ok": True, "result": [{"update_id": 1, "message": {"chat": {"id": chat_id}, "text": "hi"}}]}


def run(tmp_path, asks, secrets, result=None):
    s = Script(asks, secrets)
    with respx.mock:
        respx.post(f"https://api.telegram.org/bot{TOKEN}/getUpdates").respond(
            200, json=result or updates()
        )
        with httpx.Client() as http:
            run_setup(tmp_path, s.prompter, http)
    return s


FULL = CONFIG + ["", *ANSWERS]  # config, chat-id Enter, answers


def test_fresh_config_is_written_and_loads(tmp_path):
    run(tmp_path, FULL, [KEY, TOKEN])
    assert load_config(tmp_path / "config.yaml").llm.provider == "openai_compat"


def test_rerun_keeps_existing_files(tmp_path):
    run(tmp_path, FULL, [KEY, TOKEN])
    before = {n: (tmp_path / n).read_bytes() for n in ("config.yaml", "answers.yaml", ".env")}
    s = run(tmp_path, [], [])
    assert s.calls == 0
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
            run_setup(tmp_path, s.prompter, http)
    assert order[0][0] == "say" and "may use your prompts" in order[0][1]
    assert order[1] == ("secret", "AI Studio key: ")


def test_chat_id_discovered(tmp_path):
    run(tmp_path, FULL, [KEY, TOKEN])
    assert dotenv_values(tmp_path / ".env")["TELEGRAM_CHAT_ID"] == "4242"


def test_no_message_leaves_chat_id_unset(tmp_path):
    s = run(tmp_path, FULL, [KEY, TOKEN], result={"ok": True, "result": []})
    assert not dotenv_values(tmp_path / ".env").get("TELEGRAM_CHAT_ID")
    assert any("No message found" in m for m in s.said)


def test_answers_written(tmp_path):
    run(tmp_path, FULL, [KEY, TOKEN])
    a = load_answers(tmp_path / "answers.yaml")
    assert a.work_authorization == "EU citizen" and a.salary_max_eur == 60000
    assert a.relocation is False and a.remote is True
    assert a.gender == a.ethnicity == a.disability == a.veteran == "Prefer not to say"


def test_bad_int_is_reasked(tmp_path):
    asks = CONFIG + [""] + ["EU", "n", "1 month", "lots", "40000", "60000", "n", "y", "", "", "", ""]
    s = run(tmp_path, asks, [KEY, TOKEN])
    assert load_answers(tmp_path / "answers.yaml").salary_min_eur == 40000
    assert any("whole number" in m for m in s.said)


def test_secrets_never_echoed(tmp_path):
    s = run(tmp_path, FULL, [KEY, TOKEN])
    assert not any(KEY in m or TOKEN in m for m in s.said)
    assert yaml.safe_load((tmp_path / "config.yaml").read_text())["profile"]["name"] == "Hugo"


def test_max_salary_below_min_is_reasked(tmp_path):
    asks = CONFIG + [""] + ["EU", "n", "1 month", "40000", "30000", "60000", "n", "y", "", "", "", ""]
    s = run(tmp_path, asks, [KEY, TOKEN])
    assert load_answers(tmp_path / "answers.yaml").salary_max_eur == 60000
    assert any("at least 40000" in m for m in s.said)
