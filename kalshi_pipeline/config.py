"""Loads and validates tracked_markets.yml.

Returns a flat list of ticker strings for the collector to poll; the per-entry
``note`` field is documentation only and is not surfaced.
"""
from __future__ import annotations

from pathlib import Path

import yaml


class ConfigError(Exception):
    """Raised when tracked_markets.yml is structurally invalid."""


def load_tracked_markets(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "markets" not in data:
        raise ConfigError(f"{path}: missing top-level 'markets' key")

    markets = data["markets"]
    if not isinstance(markets, list) or not markets:
        raise ConfigError(f"{path}: 'markets' must be a non-empty list")

    tickers: list[str] = []
    seen: set[str] = set()
    for i, entry in enumerate(markets):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: markets[{i}] is not a mapping")
        ticker = entry.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            raise ConfigError(f"{path}: markets[{i}] missing non-empty 'ticker'")
        if ticker in seen:
            raise ConfigError(f"{path}: duplicate ticker: {ticker}")
        seen.add(ticker)
        tickers.append(ticker)

    return tickers
