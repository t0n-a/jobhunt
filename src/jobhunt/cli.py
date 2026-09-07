import argparse
from pathlib import Path

import uvicorn

from jobhunt.config import load_config
from jobhunt.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="jobhunt")
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    config = load_config(args.config)
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port)
