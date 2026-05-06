"""Bulk-fetch daily candlesticks for the locked Test B universe.

Iterates the 98 markets in notes/test-b-universe.csv and calls
GET /series/{series_ticker}/markets/{ticker}/candlesticks for each
market with start_ts/end_ts spanning effective_window_start to
effective_window_end and period_interval=1440 (daily). Saves the raw
API response to data/candlesticks/{ticker}.json (one file per market).

Authentication is via the RSA-PSS pattern in simulator/kalshi_auth.py
per notes/simulator-design.md §2.

Retry/backoff:
- 429 and 5xx: exponential backoff (5s / 15s / 45s, max 3 retries)
- 401 / 403 / 404: hard-fail without retry

Pagination: if the candlesticks response includes a non-empty
`cursor` field, the fetcher follows it and concatenates the
`candlesticks` arrays across pages. The saved JSON has the merged
candlesticks under a single `candlesticks` key plus the original
`ticker` key, with no cursor.

Usage:
    /Users/peteryakovlev/projects/kalshi-pipeline/.venv/bin/python \\
        scripts/fetch_candlesticks.py [--smoke]

--smoke fetches only 3 representative markets for inspection before
the full bulk run:
- KXTORYMPJOINREFORM-26-TMP (short single-binary, 33-day lifespan)
- KXBALANCE-29 (long single-binary, 458-day lifespan)
- KXFEDCOMBO-26MAR-0-T0 (multi-outcome-2-4 member)

No DB access. Reads .env at repo root for KALSHI_API_KEY_ID and
KALSHI_PRIVATE_KEY_PATH.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog
from dotenv import load_dotenv

# Make `simulator` importable when running as a script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.kalshi_auth import auth_headers, load_private_key  # noqa: E402

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
UNIVERSE_CSV = PROJECT_ROOT / "notes" / "test-b-universe.csv"
CACHE_DIR = PROJECT_ROOT / "data" / "candlesticks"
HTTP_TIMEOUT_SECONDS = 30.0
RATE_LIMIT_SLEEP_SECONDS = 1.0
BACKOFF_SCHEDULE = (5, 15, 45)
PERIOD_INTERVAL = 1440  # daily

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
# Time-window parsing
# ---------------------------------------------------------------------------


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def window_to_unix(start_iso: str, end_iso: str) -> tuple[int, int]:
    """Convert ISO timestamps to (start_ts, end_ts) Unix seconds with
    a 1-day padding on each side, matching the convention used in
    scripts/build_test_b_universe.py for the reachability probe."""
    start_dt = _parse_iso(start_iso) - timedelta(days=1)
    end_dt = _parse_iso(end_iso) + timedelta(days=1)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


# ---------------------------------------------------------------------------
# Single-market fetch with retry, backoff, and pagination
# ---------------------------------------------------------------------------


class FetchError(Exception):
    """Raised when a fetch fails in a way that should not be retried
    (e.g. 4xx other than 429)."""


def fetch_one_market(
    client: httpx.Client,
    key_id: str,
    private_key,
    series_ticker: str,
    ticker: str,
    start_ts: int,
    end_ts: int,
) -> dict[str, Any]:
    """Fetch all candlesticks for one market, handling pagination
    (cursor) and 429/5xx retries with exponential backoff. Returns a
    merged response dict with `ticker` and `candlesticks` keys.

    Raises FetchError on non-retryable HTTP errors (401 / 403 / 404 /
    other 4xx) or after BACKOFF_SCHEDULE retries are exhausted.
    """
    path = f"/series/{series_ticker}/markets/{ticker}/candlesticks"
    full_path = f"/trade-api/v2{path}"
    cursor: str = ""
    merged_candles: list[dict] = []
    response_ticker: str | None = None

    page_idx = 0
    while True:
        params: dict[str, Any] = {
            "period_interval": PERIOD_INTERVAL,
            "start_ts": start_ts,
            "end_ts": end_ts,
        }
        if cursor:
            params["cursor"] = cursor

        body = _get_with_retry(client, key_id, private_key, path, full_path, params)

        if response_ticker is None:
            response_ticker = body.get("ticker")

        page_candles = body.get("candlesticks") or []
        merged_candles.extend(page_candles)

        next_cursor = body.get("cursor") or ""
        if not next_cursor or not page_candles:
            break

        cursor = next_cursor
        page_idx += 1
        if page_idx > 100:
            log.warning("pagination_cap_hit", ticker=ticker, pages=page_idx)
            break
        time.sleep(RATE_LIMIT_SLEEP_SECONDS)

    return {"ticker": response_ticker or ticker, "candlesticks": merged_candles}


def _get_with_retry(
    client: httpx.Client,
    key_id: str,
    private_key,
    path: str,
    full_path: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """One signed GET with backoff retries on 429 and 5xx. Returns
    parsed JSON body. Raises FetchError on non-retryable errors or
    backoff exhaustion."""
    for attempt, sleep_s in enumerate([0, *BACKOFF_SCHEDULE]):
        if sleep_s:
            log.info("backoff", path=path, attempt=attempt, sleep_s=sleep_s)
            time.sleep(sleep_s)
        headers = auth_headers(key_id, private_key, "GET", full_path)
        try:
            r = client.get(path, params=params, headers=headers)
        except httpx.HTTPError as e:
            log.warning("http_error", path=path, error=str(e))
            raise FetchError(f"transport error on {path}: {e}") from e

        if r.status_code == 200:
            try:
                return r.json()
            except ValueError as e:
                raise FetchError(f"non-JSON 200 body on {path}: {e}") from e

        if r.status_code == 429 or 500 <= r.status_code < 600:
            log.warning(
                "retryable_status",
                path=path,
                status=r.status_code,
                attempt=attempt,
            )
            continue

        # 4xx other than 429: hard fail
        raise FetchError(
            f"non-retryable status {r.status_code} on {path}: "
            f"{r.text[:200]}"
        )

    raise FetchError(f"exhausted backoff retries on {path}")


# ---------------------------------------------------------------------------
# Universe iteration
# ---------------------------------------------------------------------------


def load_universe() -> list[dict[str, str]]:
    with UNIVERSE_CSV.open() as f:
        return list(csv.DictReader(f))


def save_response(ticker: str, payload: dict[str, Any]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{ticker}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def run_fetch(rows: list[dict[str, str]], key_id: str, private_key) -> dict[str, Any]:
    """Fetch each market in `rows`. Returns a per-ticker outcome dict."""
    outcomes: dict[str, Any] = {}
    with httpx.Client(base_url=API_BASE, timeout=HTTP_TIMEOUT_SECONDS) as client:
        for i, row in enumerate(rows, 1):
            ticker = row["ticker"]
            series_ticker = row["series_ticker"]
            start_ts, end_ts = window_to_unix(
                row["effective_window_start"],
                row["effective_window_end"],
            )
            try:
                payload = fetch_one_market(
                    client, key_id, private_key,
                    series_ticker, ticker, start_ts, end_ts,
                )
                path = save_response(ticker, payload)
                n = len(payload["candlesticks"])
                outcomes[ticker] = {"n_candles": n, "path": str(path)}
                log.info(
                    "fetched",
                    ticker=ticker,
                    n_candles=n,
                    progress=f"{i}/{len(rows)}",
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
    parser = argparse.ArgumentParser(description="Bulk-fetch Kalshi candlesticks.")
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

    rows = load_universe()
    if args.smoke:
        rows = [r for r in rows if r["ticker"] in SMOKE_TICKERS]
        if len(rows) != len(SMOKE_TICKERS):
            log.error(
                "smoke_tickers_missing",
                expected=list(SMOKE_TICKERS),
                found=[r["ticker"] for r in rows],
            )
            return 1

    log.info("starting", n_rows=len(rows), smoke=args.smoke, cache_dir=str(CACHE_DIR))
    outcomes = run_fetch(rows, key_id, private_key)

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
