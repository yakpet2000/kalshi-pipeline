"""One-shot collector for tracked Kalshi markets.

Computes a single 15-minute UTC bucket per run, fetches each ticker's current
state, builds ``SnapshotRow`` objects, and writes them in one batch via
``db.insert_snapshots``. Per-ticker errors are logged and skipped; the run
continues regardless.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import structlog
from pydantic import ValidationError

from kalshi_pipeline import db
from kalshi_pipeline.config import load_tracked_markets
from kalshi_pipeline.kalshi_client import KalshiClient

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CollectorRunSummary:
    observed_at: datetime
    attempted: int
    succeeded: int
    failed: int
    inserted: int


def run_once(tracked_markets_path: Path) -> CollectorRunSummary:
    observed_at = db.floor_to_15min(datetime.now(UTC))
    tickers = load_tracked_markets(tracked_markets_path)

    successful_rows: list[db.SnapshotRow] = []
    failed = 0

    with KalshiClient() as client:
        for ticker in tickers:
            t0 = time.perf_counter()
            try:
                market = client.get_market(ticker)
            except (httpx.HTTPStatusError, httpx.RequestError, ValidationError) as e:
                latency_ms = round((time.perf_counter() - t0) * 1000, 2)
                log.warning(
                    "ticker_fetch_failed",
                    ticker=ticker,
                    outcome="error",
                    error_class=type(e).__name__,
                    latency_ms=latency_ms,
                )
                failed += 1
                continue
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            log.info(
                "ticker_fetched",
                ticker=ticker,
                outcome="success",
                latency_ms=latency_ms,
            )
            successful_rows.append(
                db.SnapshotRow(
                    ticker=ticker,
                    observed_at=observed_at,
                    updated_time=market.updated_time,
                    yes_bid_dollars=market.yes_bid_dollars,
                    yes_ask_dollars=market.yes_ask_dollars,
                    no_bid_dollars=market.no_bid_dollars,
                    no_ask_dollars=market.no_ask_dollars,
                    last_price_dollars=market.last_price_dollars,
                    previous_price_dollars=market.previous_price_dollars,
                    previous_yes_bid_dollars=market.previous_yes_bid_dollars,
                    previous_yes_ask_dollars=market.previous_yes_ask_dollars,
                    volume_fp=market.volume_fp,
                    volume_24h_fp=market.volume_24h_fp,
                    open_interest_fp=market.open_interest_fp,
                    yes_bid_size_fp=market.yes_bid_size_fp,
                    yes_ask_size_fp=market.yes_ask_size_fp,
                    raw_payload=market.model_dump(mode="json"),
                )
            )

    if successful_rows:
        with db.connect() as conn:
            inserted = db.insert_snapshots(conn, successful_rows)
    else:
        inserted = 0

    summary = CollectorRunSummary(
        observed_at=observed_at,
        attempted=len(tickers),
        succeeded=len(successful_rows),
        failed=failed,
        inserted=inserted,
    )
    log.info(
        "collector_run_complete",
        observed_at=observed_at.isoformat(),
        attempted=summary.attempted,
        succeeded=summary.succeeded,
        failed=summary.failed,
        inserted=summary.inserted,
    )
    return summary
