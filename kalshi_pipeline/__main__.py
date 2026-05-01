"""CLI entry point for kalshi-pipeline.

Subcommand ``collect`` runs the collector once. Configures structlog (dev
console vs JSON renderer) based on the ``ENV`` environment variable.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import structlog

from kalshi_pipeline.collector import run_once

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACKED_MARKETS_PATH = REPO_ROOT / "tracked_markets.yml"


def _configure_logging() -> None:
    env = os.environ.get("ENV", "dev")
    processors: list = [
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if env == "dev":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())
    structlog.configure(processors=processors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kalshi_pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("collect", help="Run one collector pass.")
    args = parser.parse_args(argv)

    _configure_logging()

    if args.command == "collect":
        summary = run_once(TRACKED_MARKETS_PATH)
        return 0 if summary.succeeded > 0 else 1

    raise AssertionError(f"unhandled subcommand: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
