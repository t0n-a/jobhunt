import argparse
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from jobhunt.config import load_config
from jobhunt.web.app import create_app


def load_env(dotenv_path: Path | str | None = None) -> bool:
    """Load environment variables from .env without overriding existing ones."""
    target = Path(dotenv_path) if dotenv_path is not None else Path.cwd() / ".env"
    return load_dotenv(dotenv_path=target, override=False)


def run_scan_command(config) -> str:
    """Run one scan and return the one-line summary. Returns rather than prints
    so the outcome is assertable in a test."""
    import os

    from jobhunt.db import create_engine_for, init_db
    from jobhunt.llm import build_provider
    from jobhunt.scan import build_sources, run_scan

    engine = create_engine_for(config.data_dir / "jobhunt.db")
    init_db(engine)
    sources = build_sources(config.sources, os.environ.get("ITJOBS_API_KEY"))
    if not sources:
        return (
            "No sources enabled. Add company board slugs under `sources:` in "
            "config.yaml, or enable an aggregator."
        )
    result = run_scan(engine, sources, build_provider(config.llm))
    parts = [
        f"fetched={result.fetched}",
        f"jobs={result.jobs}",
        f"scored={result.scored}",
    ]
    if result.failed_sources:
        parts.append(f"failed={','.join(result.failed_sources)}")
    if result.timed_out_sources:
        parts.append(f"timed_out={','.join(result.timed_out_sources)}")
    return " ".join(parts)


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(prog="jobhunt")
    parser.add_argument("command", choices=["serve", "scan"])
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    config = load_config(args.config)

    if args.command == "scan":
        print(run_scan_command(config))
        return

    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port)
