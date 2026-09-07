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


def main() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    parser = argparse.ArgumentParser(prog="jobhunt")
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    config = load_config(args.config)
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port)
