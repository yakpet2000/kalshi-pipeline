"""End-to-end Test B simulator. Sub-stage 2b.7.

Methodology lock fires when this script runs against the locked
universe table and produces notes/test-b-positions.csv per
notes/simulator-design.md §7. After this commit, decisions in §3.1
through §3.7 cannot be revised. Implementation bug fixes against
those decisions remain legitimate.

Wires together the upstream sub-stages:
- 2b.1: simulator/tbill.py (FRED DGS3MO lookup)
- 2b.2: data/candlesticks/{ticker}.json (cached candles)
- 2b.2.1: data/market-details/{ticker}.json (cached per-market
   detail; provides settlement_value_dollars for the void rule)
- 2b.3: simulator/daily_check.py (per-market PostEvents)
- 2b.4: simulator/cap_layer.py ($30K cap + tiebreaker)
- 2b.5: voided handling co-located in daily_check.py
- 2b.6: simulator/output.py (CSV writer + diagnostics)

Two pre-registered sanity checks (added in this sub-stage):

(a) Schema check on cached market-details payloads. Before invoking
    the simulator, assert every cached JSON file contains the keys
    classify_settlement and build_market_meta read. This catches
    input-data drift at the earliest possible point. The original
    2b.7 bug — assuming `settlement_value_dollars` was in
    candidate-universe.csv when it was not — would have surfaced
    here as a clear "missing key" error rather than as uniformly-
    voided output.

(b) Voided-count sanity check after simulation. The 2b.5 universe
    audit and the 2b.2.1 re-verification both confirmed
    44 yes / 54 no / 0 voided. The simulator must reproduce the
    `0 voided` half. If the funnel reports voided_count != 0, the
    integration aborts before writing the CSV. This catches
    classifier garbage even when input fields exist.

Both checks are pre-registered checks on a known prior, not
verdict-relevant statistics. Fill rates, P&L distributions, and
analysis-stage numbers are not inspected at this stage.

blocked_by_filter handling: stubbed at zero. The fetcher's ±1 day
padding around effective_window_start/end did not produce extra
candles in practice (Kalshi returns candles only within actual
market lifespan), so the daily-check engine's iteration is
implicitly bounded by the effective window. v0.1 has no scheduled-
event filter (universe-construction.md §7 limitation #2). Hard
data inconsistencies (missing candle file, missing market-details
file) raise rather than silently being labeled blocked_by_filter.

Usage:
    /Users/peteryakovlev/projects/kalshi-pipeline/.venv/bin/python \\
        scripts/simulate_test_b.py

No CLI args. No DB access. No live API calls — all reads come from
cached files. Re-fetching is a separate operation
(scripts/fetch_candlesticks.py, scripts/fetch_market_details.py,
scripts/fetch_dgs3mo.py).
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import structlog

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.cap_layer import apply_cap_layer  # noqa: E402
from simulator.daily_check import PostEvent, run_market  # noqa: E402
from simulator.et_bucket import ET  # noqa: E402
from simulator.output import (  # noqa: E402
    _format_diagnostics_text,
    write_diagnostics,
    write_positions_csv,
)
from simulator.tbill import tbill_rate  # noqa: E402

UNIVERSE_CSV = PROJECT_ROOT / "notes" / "test-b-universe.csv"
CANDLES_DIR = PROJECT_ROOT / "data" / "candlesticks"
MARKET_DETAILS_DIR = PROJECT_ROOT / "data" / "market-details"
OUTPUT_POSITIONS = PROJECT_ROOT / "notes" / "test-b-positions.csv"
OUTPUT_DIAGNOSTICS = PROJECT_ROOT / "notes" / "test-b-diagnostics.txt"

# Required keys in the cached market-details payload's `market` sub-dict.
# These are the fields classify_settlement and build_market_meta read.
# Some fields may be null/empty for non-voided markets; this check
# verifies the keys EXIST so a future voided market doesn't fail
# mid-simulation with a KeyError.
REQUIRED_MARKET_KEYS: tuple[str, ...] = (
    "result",
    "settlement_value_dollars",
    "settlement_ts",          # void-announcement proxy (voided-market-detection.md §4)
    "expected_expiration_time",  # original expected settlement (§4)
    "expiration_time",        # fallback for expected (§4)
    "status",                 # defensive: should be "finalized" for universe markets
)

ENV = os.environ.get("ENV", "dev")
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if ENV == "dev"
        else structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Errors raised by the integration script's runtime checks
# ---------------------------------------------------------------------------


class SchemaCheckFailure(RuntimeError):
    """Raised by preflight_schema_check when a cached market-details
    payload is missing required keys. Indicates input-data drift."""


class VoidedCountSanityFailure(RuntimeError):
    """Raised by sanity_check_voided_count when the simulator's
    funnel reports voided_count != 0, contradicting the 2b.5 audit."""


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _utc_iso_to_et_date(iso_str: str) -> date:
    return _parse_iso(iso_str).astimezone(ET).date()


def load_universe() -> list[dict[str, str]]:
    with UNIVERSE_CSV.open() as f:
        return list(csv.DictReader(f))


def load_market_detail(ticker: str) -> dict[str, Any]:
    """Load the cached per-market detail JSON for one ticker. Raises
    FileNotFoundError if absent."""
    path = MARKET_DETAILS_DIR / f"{ticker}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"missing market-details cache for ticker={ticker!r}: {path}; "
            f"run scripts/fetch_market_details.py"
        )
    with path.open() as f:
        return json.load(f)


def load_candles(ticker: str) -> list[dict]:
    path = CANDLES_DIR / f"{ticker}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"missing candle cache for ticker={ticker!r}: {path}; "
            f"run scripts/fetch_candlesticks.py"
        )
    with path.open() as f:
        return (json.load(f).get("candlesticks") or [])


# ---------------------------------------------------------------------------
# Sanity check (a): schema check on cached market-details inputs
# ---------------------------------------------------------------------------


def preflight_schema_check(universe_tickers: list[str]) -> None:
    """For every universe ticker, verify the cached market-details
    JSON has all REQUIRED_MARKET_KEYS in its `market` sub-dict.
    Raises SchemaCheckFailure on the first ticker that fails.

    This is the test that would have caught the original 2b.7 bug
    (assuming `settlement_value_dollars` was in
    candidate-universe.csv when it was not) at the earliest point —
    before the simulator runs."""
    missing_by_ticker: dict[str, list[str]] = {}
    for ticker in universe_tickers:
        try:
            payload = load_market_detail(ticker)
        except FileNotFoundError as e:
            raise SchemaCheckFailure(str(e)) from e
        market = payload.get("market") or {}
        if not isinstance(market, dict):
            raise SchemaCheckFailure(
                f"ticker={ticker!r}: payload has no 'market' sub-dict"
            )
        missing = [k for k in REQUIRED_MARKET_KEYS if k not in market]
        if missing:
            missing_by_ticker[ticker] = missing
    if missing_by_ticker:
        sample = list(missing_by_ticker.items())[:3]
        raise SchemaCheckFailure(
            f"market-details cache missing required keys for "
            f"{len(missing_by_ticker)} ticker(s); sample: {sample}"
        )
    log.info(
        "preflight_schema_check_ok",
        n_tickers=len(universe_tickers),
        required_keys=list(REQUIRED_MARKET_KEYS),
    )


# ---------------------------------------------------------------------------
# Settlement classification per voided-market-detection.md §3
# ---------------------------------------------------------------------------


def classify_settlement(market: dict[str, Any]) -> str:
    """Apply notes/voided-market-detection.md §3 OR rule. Returns
    'yes', 'no', or 'voided'. Raises ValueError on ambiguous cases
    (status not finalized, etc.) — those should not appear in the
    locked universe."""
    if market.get("status") != "finalized":
        raise ValueError(
            f"non-finalized status in market detail: ticker={market.get('ticker')!r} "
            f"status={market.get('status')!r}"
        )
    result = (market.get("result") or "").strip()
    sv = (market.get("settlement_value_dollars") or "").strip()
    if result == "":
        return "voided"
    if sv not in ("0.0000", "1.0000"):
        return "voided"
    if result in ("yes", "no") and sv in ("0.0000", "1.0000"):
        return result
    raise ValueError(
        f"ambiguous settlement classification for ticker={market.get('ticker')!r}: "
        f"result={result!r} sv={sv!r}"
    )


def build_market_meta(
    universe_row: dict[str, str],
    market: dict[str, Any],
    settlement_outcome: str,
) -> dict:
    """Build market_meta consumed by daily_check.run_market. For
    voided markets, populates expected_settlement_date and
    void_announcement_date per voided-market-detection.md §4."""
    meta: dict = {
        "ticker": universe_row["ticker"],
        "event_ticker": universe_row["event_ticker"],
        "series_ticker": universe_row["series_ticker"],
        "primary_bucket": universe_row["primary_bucket"],
        "structure": universe_row["structure"],
        "settlement_outcome": settlement_outcome,
    }
    if settlement_outcome == "voided":
        # Original expected settlement date: expected_expiration_time,
        # falling back to expiration_time per §4.
        eet = (market.get("expected_expiration_time") or "").strip()
        et_iso = (market.get("expiration_time") or "").strip()
        if eet:
            meta["expected_settlement_date"] = _utc_iso_to_et_date(eet)
        elif et_iso:
            meta["expected_settlement_date"] = _utc_iso_to_et_date(et_iso)
        else:
            raise ValueError(
                f"voided ticker {universe_row['ticker']!r} has neither "
                f"expected_expiration_time nor expiration_time"
            )
        # Void announcement proxy: settlement_ts. Null is allowed
        # (per §4, the rule falls back to expected_settlement_date).
        sts = (market.get("settlement_ts") or "").strip()
        meta["void_announcement_date"] = (
            _utc_iso_to_et_date(sts) if sts else None
        )
    return meta


# ---------------------------------------------------------------------------
# Sanity check (b): voided count must be 0 per the 2b.5 audit
# ---------------------------------------------------------------------------


def sanity_check_voided_count(diagnostics: dict[str, Any]) -> None:
    """Per the 2b.5 universe audit (44 yes / 54 no / 0 voided) and
    the 2b.2.1 re-verification, voided_count must be 0. If non-zero,
    the simulator's classification has degraded and the output is
    not safe to commit."""
    voided = diagnostics.get("voided_count", -1)
    if voided != 0:
        raise VoidedCountSanityFailure(
            f"funnel reports voided_count={voided}, expected 0 "
            f"per 2b.5 audit + 2b.2.1 re-verification. Refusing to "
            f"write outputs; classifier has degraded."
        )
    log.info("voided_count_sanity_ok", voided_count=voided)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    log.info("simulator_starting")

    universe_rows = load_universe()
    universe_tickers = [r["ticker"] for r in universe_rows]
    log.info("loaded_universe", n=len(universe_rows))

    # ---- Pre-flight check (a): schema check on inputs ----
    preflight_schema_check(universe_tickers)

    # ---- Phase 1: per-market run ----
    all_events: list[PostEvent] = []
    settlement_outcome_counts = {"yes": 0, "no": 0, "voided": 0}
    for row in universe_rows:
        ticker = row["ticker"]
        payload = load_market_detail(ticker)
        market = payload.get("market") or {}
        settlement_outcome = classify_settlement(market)
        settlement_outcome_counts[settlement_outcome] += 1
        market_meta = build_market_meta(row, market, settlement_outcome)
        candles = load_candles(ticker)
        events = run_market(candles, market_meta, tbill_rate)
        all_events.extend(events)
    log.info("per_market_done",
             total_events=len(all_events),
             outcomes=settlement_outcome_counts)

    # ---- Phase 2: cap layer ----
    open_time_by_ticker = {r["ticker"]: r["open_time"] for r in universe_rows}
    final_events = apply_cap_layer(all_events, open_time_by_ticker)
    log.info("cap_layer_done", final_events=len(final_events))

    # ---- Phase 3: write outputs ----
    n_rows = write_positions_csv(final_events, OUTPUT_POSITIONS)
    log.info("wrote_positions", n_rows=n_rows, path=str(OUTPUT_POSITIONS))

    diag = write_diagnostics(final_events, OUTPUT_DIAGNOSTICS)
    log.info("wrote_diagnostics", path=str(OUTPUT_DIAGNOSTICS))

    # ---- Post-run sanity check (b): voided count must be 0 ----
    # Done AFTER write_positions_csv so the bad output, if produced,
    # is on disk for manual inspection. The script still raises and
    # exits non-zero so CI / automation knows the run failed.
    sanity_check_voided_count(diag)

    # Stdout: per simulator-design.md §5
    print()
    print(_format_diagnostics_text(diag), end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())
