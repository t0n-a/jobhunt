"""First-run setup: config.yaml, .env keys, Telegram chat id, standard answers."""

import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml
from dotenv import dotenv_values, load_dotenv, set_key
from sqlmodel import Session

from jobhunt.answers import StandardAnswers, save_answers
from jobhunt.config import Config, load_config
from jobhunt.db import create_engine_for, init_db
from jobhunt.llm import build_provider
from jobhunt.profile.importers.cv import import_cv
from jobhunt.profile.store import AtomStore
from jobhunt.telegram import discover_chat_id
from jobhunt.timers import install_timers

# Free tier prompts may be used by Google; the candidate must know before pasting a key.
KEY_WARNING = (
    "Get a free key at aistudio.google.com/apikey. On the free tier, Google may use your "
    "prompts and responses to improve its products, and your CV and profile are in those "
    "prompts. Use a paid key if that is not acceptable."
)
def _new_code() -> str:
    return f"{secrets.randbelow(900_000) + 100_000}"


AI_STUDIO_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


@dataclass
class Prompter:
    ask: Callable[[str], str]
    secret: Callable[[str], str]
    say: Callable[[str], None]


def setup_config(root: Path, p: Prompter) -> bool:
    path = root / "config.yaml"
    if path.exists():
        p.say("config.yaml found; keeping it.")
        return False
    profile = {
        "name": p.ask("Your name: ").strip(),
        "email": p.ask("Your email: ").strip(),
        "location": p.ask("Your location: ").strip(),
    }
    if github := p.ask("GitHub username (blank to skip): ").strip():
        profile["github_username"] = github
    data = {
        "profile": profile,
        "llm": {
            "provider": "openai_compat",
            "model": "gemini-flash-latest",
            "min_interval_seconds": 6,
            "cache_dir": "./data/llm_cache",
        },
        "data_dir": "./data",
    }
    Config.model_validate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return True


def setup_keys(root: Path, p: Prompter, http: httpx.Client) -> None:
    env = root / ".env"
    env.touch()
    os.chmod(env, 0o600)

    def missing(key: str) -> bool:
        return not dotenv_values(env).get(key)

    def put(key: str, value: str) -> None:
        set_key(env, key, value, quote_mode="never")

    if missing("OPENAI_API_KEY"):
        p.say(KEY_WARNING)
        if key := p.secret("AI Studio key: ").strip():
            put("OPENAI_API_KEY", key)
            put("OPENAI_BASE_URL", AI_STUDIO_URL)
    if missing("TELEGRAM_BOT_TOKEN"):
        p.say("Create a bot: message @BotFather on Telegram, send /newbot, and paste the token here.")
        if token := p.secret("Bot token: ").strip():
            put("TELEGRAM_BOT_TOKEN", token)
    token = dotenv_values(env).get("TELEGRAM_BOT_TOKEN")
    if token and missing("TELEGRAM_CHAT_ID"):
        code = _new_code()
        p.ask(f"Send this code to your new bot from your own Telegram account: {code}\n"
              "Then press Enter.")
        if chat_id := discover_chat_id(token, http, code):
            put("TELEGRAM_CHAT_ID", chat_id)
        else:
            p.say("The code was not found. Run jobhunt setup again and send the new code.")


def _ask_field(label: str, annotation, p: Prompter, minimum: int | None = None):
    if annotation is bool:
        while True:
            raw = p.ask(f"{label} (y/n): ").strip().lower()
            if raw in ("y", "n"):
                return raw == "y"
    if annotation is int:
        while True:
            try:
                value = int(p.ask(f"{label}: ").strip().replace(",", ""))
            except ValueError:
                p.say("Please enter a whole number.")
                continue
            if minimum is not None and value < minimum:
                p.say(f"Please enter at least {minimum}.")
                continue
            return value
    return p.ask(f"{label}: ").strip()


def setup_answers(root: Path, p: Prompter) -> bool:
    path = root / "answers.yaml"
    if path.exists():
        return False
    values = {}
    for name, field in StandardAnswers.model_fields.items():
        label = name.replace("_", " ").capitalize()
        if field.is_required():
            # The maximum salary can't be below the minimum the candidate just gave.
            minimum = values.get("salary_min_eur") if name == "salary_max_eur" else None
            values[name] = _ask_field(label, field.annotation, p, minimum)
        elif field.annotation is str and (typed := p.ask(f"{label} [{field.default}]: ").strip()):
            values[name] = typed
    save_answers(path, StandardAnswers.model_validate(values))
    return True


def setup_evidence(root: Path, p: Prompter, llm) -> int:
    config = load_config(root / "config.yaml")
    data_dir = root / config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine_for(data_dir / "jobhunt.db")
    init_db(engine)
    with Session(engine) as session:
        store = AtomStore(session)
        if existing := len(store.confirmed_only()):
            p.say(f"You have {existing} confirmed facts; add more any time at /atoms.")
            return 0
        path = None
        for _ in range(2):
            raw = p.ask("Path to your CV (.pdf, .docx or .txt), or Enter to skip: ").strip()
            if not raw:
                return 0
            candidate = Path(raw).expanduser()
            if candidate.is_file() and candidate.suffix.lower() in (".pdf", ".docx", ".txt"):
                path = candidate
                break
            p.say("That file was not found or is not a .pdf, .docx or .txt.")
        if path is None:
            return 0
        store.add_many(import_cv(path, llm))
        confirmed = 0
        for atom in store.list(confirmed=False):
            p.say(f"{atom.title}\n{atom.description}\nSkills: {', '.join(atom.skills)}")
            while (c := p.ask("[y] true, keep it  [n] not true, delete  [s] skip  [q] stop: ")
                   .strip().lower()) not in ("y", "n", "s", "q"):
                pass
            if c == "q":
                break
            if c == "y":
                store.update(atom.id, confirmed=True)
                confirmed += 1
            elif c == "n":
                store.delete(atom.id)
    p.say(f"{confirmed} facts confirmed. Review the rest any time at /atoms (jobhunt serve).")
    return confirmed


def run_setup(root: Path, p: Prompter, http: httpx.Client, *, llm=None, install=install_timers):
    setup_config(root, p)
    setup_keys(root, p, http)
    setup_answers(root, p)
    try:
        if llm is None:
            load_dotenv(root / ".env", override=True)
            llm = build_provider(load_config(root / "config.yaml").llm)
        setup_evidence(root, p, llm)
    except Exception as exc:  # noqa: BLE001 - a bad key or CV must not skip the timers question
        p.say(f"CV import failed: {exc}. You can import it later at /import.")
    if p.ask("Install the daily and 5-minute timers now? [y/N] ").strip().lower() == "y":
        p.say(install(root))
