import argparse
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv

from jobhunt.config import load_config
from jobhunt.web.app import create_app


def load_env(dotenv_path: Path | str | None = None) -> bool:
    """Load environment variables from .env without overriding existing ones."""
    target = Path(dotenv_path) if dotenv_path is not None else Path.cwd() / ".env"
    return load_dotenv(dotenv_path=target, override=False)


def scan_result(config):
    """Run one scan; None when no sources are enabled."""
    import os

    from jobhunt.db import create_engine_for, init_db
    from jobhunt.llm import build_provider
    from jobhunt.scan import build_sources, run_scan

    engine = create_engine_for(config.data_dir / "jobhunt.db")
    init_db(engine)
    sources = build_sources(config.sources, os.environ.get("ITJOBS_API_KEY"))
    if not sources:
        return None
    with httpx.Client(timeout=10) as client:
        return run_scan(
            engine,
            sources,
            build_provider(config.llm),
            ranking=config.ranking,
            liveness_client=client,
        )


def summarize_scan(result) -> str:
    if result is None:
        return (
            "No sources enabled. Add company board slugs under `sources:` in "
            "config.yaml, or enable an aggregator."
        )
    parts = [
        f"fetched={result.fetched}",
        f"jobs={result.jobs}",
        f"scored={result.scored}",
    ]
    if result.unscored_remaining:
        parts.append(f"unscored={result.unscored_remaining}")
    if result.filtered:
        parts.append(f"filtered={result.filtered}")
    if result.expired:
        parts.append(f"expired={result.expired}")
    if result.failed_sources:
        parts.append(f"failed={','.join(result.failed_sources)}")
    if result.timed_out_sources:
        parts.append(f"timed_out={','.join(result.timed_out_sources)}")
    line = " ".join(parts)
    if result.scoring_skipped:
        line += f"\n  Scoring skipped: {result.scoring_skipped}."
        line += "\n  Build your profile first: run `jobhunt serve` and use /counselor."
    return line


def run_scan_command(config) -> str:
    """Run one scan and return the one-line summary. Returns rather than prints
    so the outcome is assertable in a test."""
    return summarize_scan(scan_result(config))


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(prog="jobhunt")
    parser.add_argument("command", choices=["serve", "scan", "today", "notify", "shortlist", "tick", "daily", "install-timers", "setup"])
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "install-timers":
        from jobhunt.timers import install_timers

        print(install_timers(Path.cwd()))
        return

    if args.command == "setup":
        import getpass

        from jobhunt.setup import Prompter, run_setup

        with httpx.Client(timeout=15) as http:
            run_setup(Path.cwd(), Prompter(ask=input, secret=getpass.getpass, say=print), http)
        return

    config = load_config(args.config)

    if args.command == "scan":
        print(run_scan_command(config))
        return

    if args.command == "shortlist":
        from sqlmodel import Session

        from jobhunt.db import create_engine_for, init_db
        from jobhunt.ranking.shortlist import render_line, shortlist

        engine = create_engine_for(config.data_dir / "jobhunt.db")
        init_db(engine)
        with Session(engine) as db:
            ranked = shortlist(db, config.ranking, limit=args.limit)
            print(
                "\n".join(render_line(r) for r in ranked)
                or "Nothing on the shortlist yet. Run `jobhunt scan` first."
            )
        return

    if args.command in ("today", "notify"):
        import os

        from sqlmodel import Session

        from jobhunt.coach.engine import Coach
        from jobhunt.coach.models import Energy
        from jobhunt.coach.notify import render_task_line, send_telegram
        from jobhunt.db import create_engine_for, init_db
        from jobhunt.llm import LLMError, build_provider

        engine = create_engine_for(config.data_dir / "jobhunt.db")
        init_db(engine)
        with Session(engine) as db:
            coach = Coach(
                db,
                build_provider(config.llm),
                github_user=config.profile.github_username,
                github_token=os.environ.get("GITHUB_TOKEN"),
            )
            line = render_task_line(coach.today(Energy.NORMAL))

        if args.command == "today":
            print(line)
            return

        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to use notify.")
            return
        print("sent" if send_telegram(token, chat_id, line) else "send failed")
        return

    if args.command == "tick":
        import os
        from datetime import UTC, datetime

        from sqlmodel import Session

        from jobhunt.apply import commands
        from jobhunt.db import create_engine_for, init_db
        from jobhunt.telegram import TelegramClient

        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to use tick.")
            return
        engine = create_engine_for(config.data_dir / "jobhunt.db")
        init_db(engine)
        with Session(engine) as db, httpx.Client(timeout=15) as http:
            handled = commands.run_tick(
                db, TelegramClient(token, chat_id, http), config, datetime.now(UTC)
            )
        print(f"handled={handled}")
        return

    if args.command == "daily":
        import os
        from datetime import UTC, datetime

        from jobhunt.apply.daily import run_daily
        from jobhunt.db import create_engine_for, init_db
        from jobhunt.llm import LLMError, build_provider
        from jobhunt.telegram import TelegramClient

        engine = create_engine_for(config.data_dir / "jobhunt.db")
        init_db(engine)
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        try:
            llm = build_provider(config.llm)
        except LLMError:  # no key: the digest and follow-ups don't need the LLM
            llm = None
        with httpx.Client(timeout=15) as http:
            client = TelegramClient(token, chat_id, http) if token and chat_id else None
            print(
                run_daily(
                    config,
                    engine=engine,
                    llm=llm,
                    client=client,
                    scan=lambda: scan_result(config),
                    now=datetime.now(UTC),
                )
            )
        return

    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port)
