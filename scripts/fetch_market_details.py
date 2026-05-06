"""Bulk-fetch per-market details for the locked Test B universe.

Sub-stage 2b.2.1. Captures the per-market detail fields needed by
the simulator's voided-market classification per
notes/voided-market-detection.md §3 — specifically
`settlement_value_dollars`, which is not present in
notes/candidate-universe.csv.

Iterates the 98 markets in notes/test-b-universe.csv and calls
GET /markets/{ticker} for each, saving the raw API response to
data/market-details/{ticker}.json (one file per market).

Authenticated via the RSA-PSS pattern in simulator/kalshi_auth.py.
Retry/backoff: exponential backoff (5s/15s/45s, max 3 retries) on
HTTP 429 and 5xx; hard-fail on other 4xx (e.g., 404 for the rare
unreachable ticker — Stage 1a §6 gap #2 documented ~10-15%
unreachability though Stage 1b's universe filtered them out).

Why a separate fetcher: candidate-universe.csv was built before
notes/voided-market-detection.md was written and didn't surface
`settlement_value_dollars`. Re-fetching per-market detail is the
clean path that preserves the locked OR rule in
voided-market-detection.md §3 (result-invalid OR
settlement_value_dollars-not-in-{0,1}) without degrading the
detection.

Usage:
    /Users/peteryakovlev/projects/kalshi-pipeline/.venv/bin/python \\
        scripts/fetch_market_details.py [--smoke]

--smoke fetches only 3 representative markets (same selection as
scripts/fetch_candlesticks.py for diagnostic continuity):
- KXTORYMPJOINREFORM-26-TMP (short single-binary)
- KXBALANCE-29 (long single-binary)
- KXFEDCOMBO-26MAR-0-T0 (multi-outcome-2-4 member)

No DB access. Reads .env at repo root for KALSHI_API_KEY_ID and
KALSHI_PRIVATE_KEY_PATH. data/market-details/ is gitignored via
the existing data/ rule; the per-ticker manifest is committed at
notes/market-details-manifest.txt.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import structlog
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.kalshi_auth import auth_headers, load_private_key  # noqa: E402

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
UNIVERSE_CSV = PROJECT_ROOT / "notes" / "test-b-universe.csv"
CACHE_DIR = PROJECT_ROOT / "data" / "market-details"
HTTP_TIMEOUT_SECONDS = 30.0
RATE_LIMIT_SLEEP_SECONDS = 1.0
BACKOFF_SCHEDULE = (5, 15, 45)

SMOKE_TICKERS = (
    "KXTORYMPJOINREFORM-26-TMP",
    "KXBALANCE-29",
    "KXFEDCOMBO-26MAR-0-T0",
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
# Single-market fetch with retry / backoff
# ---------------------------------------------------------------------------


class FetchError(Exception):
    """Raised when a fetch fails in a way that should not be retried
    (e.g. 4xx other than 429)."""


def fetch_one_market(
    client: httpx.Client,
    key_id: str,
    private_key,
    ticker: str,
) -> dict[str, Any]:
    """Fetch /markets/{ticker} once with 429/5xx backoff retries.
    Returns the parsed JSON body. Raises FetchError on non-retryable
    HTTP errors or after BACKOFF_SCHEDULE is exhausted."""
    path = f"/markets/{ticker}"
    full_path = f"/trade-api/v2{path}"

    for attempt, sleep_s in enumerate([0, *BACKOFF_SCHEDULE]):
        if sleep_s:
            log.info("backoff", ticker=ticker, attempt=attempt, sleep_s=sleep_s)
            time.sleep(sleep_s)
        headers = auth_headers(key_id, private_key, "GET", full_path)
        try:
            r = client.get(path, headers=headers)
        except httpx.HTTPError as e:
            log.warning("http_error", ticker=ticker, error=str(e))
            raise FetchError(f"transport error on {path}: {e}") from e

        if r.status_code == 200:
            try:
                return r.json()
            except ValueError as e:
                raise FetchError(f"non-JSON 200 body on {path}: {e}") from e

        if r.status_code == 429 or 500 <= r.status_code < 600:
            log.warning(
                "retryable_status",
                ticker=ticker,
                status=r.status_code,
                attempt=attempt,
            )
            continue

        # 4xx other than 429: hard fail
        raise FetchError(
            f"non-retryable status {r.status_code} on {path}: {r.text[:200]}"
        )

    raise FetchError(f"exhausted backoff retries on {path}")


# ---------------------------------------------------------------------------
# Universe iteration & cache writing
# ---------------------------------------------------------------------------


def load_universe_tickers() -> list[str]:
    with UNIVERSE_CSV.open() as f:
        return [r["ticker"] for r in csv.DictReader(f)]


def save_response(ticker: str, payload: dict[str, Any]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{ticker}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def run_fetch(tickers: list[str], key_id: str, private_key) -> dict[str, Any]:
    outcomes: dict[str, Any] = {}
    with httpx.Client(base_url=API_BASE, timeout=HTTP_TIMEOUT_SECONDS) as client:
        for i, ticker in enumerate(tickers, 1):
            try:
                payload = fetch_one_market(client, key_id, private_key, ticker)
                path = save_response(ticker, payload)
                # Surface the field this fetcher exists to provide
                market = (payload.get("market") or {})
                sv = market.get("settlement_value_dollars")
                result = market.get("result")
                outcomes[ticker] = {
                    "path": str(path),
                    "settlement_value_dollars": sv,
                    "result": result,
                }
                log.info(
                    "fetched",
                    ticker=ticker,
                    progress=f"{i}/{len(tickers)}",
                    settlement_value_dollars=sv,
                    result=result,
                )
            except FetchError as e:
                log.error("fetch_failed", ticker=ticker, error=str(e))
                outcomes[ticker] = {"error": str(e)}
            time.sleep(RATE_LIMIT_SLEEP_SECONDS)
    return outcomes


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bulk-fetch Kalshi per-market detail for the Test B universe."
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="fetch only the 3 representative markets in SMOKE_TICKERS",
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    try:
        key_id = os.environ["KALSHI_API_KEY_ID"]
        key_path = os.environ["KALSHI_PRIVATE_KEY_PATH"]
    except KeyError as e:
        log.error("missing_env", missing=str(e))
        return 1
    private_key = load_private_key(os.path.expanduser(key_path))

    tickers = load_universe_tickers()
    if args.smoke:
        tickers = [t for t in tickers if t in SMOKE_TICKERS]
        if len(tickers) != len(SMOKE_TICKERS):
            log.error(
                "smoke_tickers_missing",
                expected=list(SMOKE_TICKERS),
                found=tickers,
            )
            return 1

    log.info("starting", n_tickers=len(tickers), smoke=args.smoke,
             cache_dir=str(CACHE_DIR))
    outcomes = run_fetch(tickers, key_id, private_key)

    n_ok = sum(1 for v in outcomes.values() if "error" not in v)
    n_fail = len(outcomes) - n_ok
    print()
    print("=" * 78)
    print(f"Fetch complete: {n_ok}/{len(outcomes)} markets fetched, {n_fail} failed")
    print(f"Cache: {CACHE_DIR}")
    if n_fail:
        print()
        print("Failures:")
        for t, v in outcomes.items():
            if "error" in v:
                print(f"  {t}: {v['error']}")
    print("=" * 78)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
