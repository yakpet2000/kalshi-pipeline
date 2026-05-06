"""Build the locked Test B universe table for Session B Stage 1b.

Reads two input artifacts (notes/candidate-universe.csv and
notes/series-bucket-assignments.csv), applies the filter pipeline
specified in notes/universe-construction.md, performs a reachability
probe against the public Kalshi candlesticks endpoint, and writes
notes/test-b-universe.csv.

Reachability is defined as the candlesticks endpoint returning HTTP 200
with a non-empty `candlesticks` array. We do not separately probe
/markets/{ticker} because Stage 1a §6 gap #2 confirmed the candlesticks
endpoint is the binding constraint.

Usage:
    /Users/peteryakovlev/projects/kalshi-pipeline/.venv/bin/python \\
        scripts/build_test_b_universe.py

No CLI args. No DB access (DATABASE_URL is not read). No authentication
(the candlesticks endpoint is public per notes/candle-data-probe.md §1).
Re-runnable: identical inputs and same Kalshi data produce a
byte-identical output CSV (rows are sorted by ticker).

Expected runtime: 5-15 minutes depending on Kalshi rate-limit pressure.
~100 markets reach the reachability step; the script sleeps 1.0s between
successful probes and exponentially backs off on HTTP 429.
"""
from __future__ import annotations

import csv
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import structlog

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NOTES_DIR = PROJECT_ROOT / "notes"

INPUT_CANDIDATE_UNIVERSE = NOTES_DIR / "candidate-universe.csv"
INPUT_SERIES_BUCKETS = NOTES_DIR / "series-bucket-assignments.csv"
OUTPUT_TEST_B_UNIVERSE = NOTES_DIR / "test-b-universe.csv"

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
HTTP_TIMEOUT_SECONDS = 30.0
RATE_LIMIT_SLEEP_SECONDS = 1.0
BACKOFF_SCHEDULE = (5, 15, 45)  # exponential backoff on HTTP 429

UNIVERSE_LOCKDATE = "2026-05-04"  # per notes/candle-data-probe.md §6 gap #5

OUTPUT_COLUMNS = [
    "ticker",
    "event_ticker",
    "series_ticker",
    "primary_bucket",
    "structure",
    "open_time",
    "expected_settlement_time",
    "effective_window_start",
    "effective_window_end",
    "lifespan_days",
    "candle_count",
    "reachable",
]

VALID_BUCKETS = {
    "macro",
    "geopolitics",
    "us_politics",
    "us_political_appointment",
    "policy_outcome_quantitative",
}

# ---------------------------------------------------------------------------
# structlog setup (env-gated, matching kalshi_pipeline/ convention per CLAUDE.md)
# ---------------------------------------------------------------------------

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
# Funnel tracking (for the §6 funnel printout)
# ---------------------------------------------------------------------------


@dataclass
class Funnel:
    """Records row counts at each filter stage for the auditable funnel."""

    raw_input: int = 0
    after_finalized: int = 0
    after_series_join: int = 0
    after_walkthrough_drop: int = 0
    after_oos_drop: int = 0
    after_lifespan: int = 0
    after_cardinality: int = 0
    after_reachability: int = 0
    structure_counts: dict[str, int] = field(default_factory=dict)
    bucket_counts: dict[str, int] = field(default_factory=dict)
    reach_attempted: int = 0
    reach_ok: int = 0
    reach_404: int = 0
    reach_empty: int = 0
    reach_other: int = 0


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_candidate_universe() -> list[dict[str, str]]:
    with INPUT_CANDIDATE_UNIVERSE.open() as f:
        rows = list(csv.DictReader(f))
    log.info("loaded_candidate_universe", n=len(rows), path=str(INPUT_CANDIDATE_UNIVERSE))
    return rows


def load_series_buckets() -> dict[str, dict[str, str]]:
    with INPUT_SERIES_BUCKETS.open() as f:
        bucket_map = {r["series_ticker"]: r for r in csv.DictReader(f)}
    log.info("loaded_series_buckets", n=len(bucket_map), path=str(INPUT_SERIES_BUCKETS))
    return bucket_map


# ---------------------------------------------------------------------------
# Filter pipeline (per notes/universe-construction.md §2)
# ---------------------------------------------------------------------------


def filter_finalized(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out = [r for r in rows if r.get("status") == "finalized"]
    log.info("filter_finalized", n_in=len(rows), n_out=len(out))
    return out


def filter_series_join(
    rows: list[dict[str, str]], bucket_map: dict[str, dict[str, str]]
) -> list[dict[str, str]]:
    out = []
    for r in rows:
        b = bucket_map.get(r.get("series_ticker", ""))
        if b is None:
            continue
        merged = dict(r)
        merged["primary_bucket"] = b.get("primary_bucket", "")
        merged["walkthrough_excluded"] = b.get("walkthrough_excluded", "false")
        merged["out_of_scope_excluded"] = b.get("out_of_scope_excluded", "false")
        out.append(merged)
    log.info("filter_series_join", n_in=len(rows), n_out=len(out))
    return out


def filter_drop_walkthrough(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out = [r for r in rows if r.get("walkthrough_excluded", "false") != "true"]
    log.info("filter_drop_walkthrough", n_in=len(rows), n_out=len(out))
    return out


def filter_drop_out_of_scope(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out = [r for r in rows if r.get("out_of_scope_excluded", "false") != "true"]
    log.info("filter_drop_out_of_scope", n_in=len(rows), n_out=len(out))
    return out


def assert_primary_bucket_populated(rows: list[dict[str, str]]) -> None:
    """Defensive invariant per notes/universe-construction.md §2 step 4."""
    offenders = [r["ticker"] for r in rows if r.get("primary_bucket", "") not in VALID_BUCKETS]
    if offenders:
        log.error(
            "primary_bucket_invariant_violated",
            offender_count=len(offenders),
            offenders_sample=offenders[:5],
        )
        raise AssertionError(
            f"After walkthrough/OOS exclusion, {len(offenders)} rows have an "
            f"empty or invalid primary_bucket. This indicates a bug in "
            f"notes/series-bucket-assignments.csv. Sample offenders: "
            f"{offenders[:5]}. Failing loudly per universe-construction.md §2."
        )
    log.info("assertion_primary_bucket_ok", n=len(rows))


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_lifespan(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """Returns (survivors, n_dropped_for_missing_dates)."""
    out = []
    dropped_missing = 0
    for r in rows:
        ot = _parse_iso(r.get("open_time", ""))
        st = _parse_iso(r.get("settle_time", "")) or _parse_iso(r.get("close_time", ""))
        if ot is None or st is None:
            dropped_missing += 1
            continue
        delta = st - ot
        lifespan_days = delta.days  # floor to integer days
        if lifespan_days < 30:
            continue
        merged = dict(r)
        merged["expected_settlement_time"] = st.isoformat().replace("+00:00", "Z")
        merged["lifespan_days"] = lifespan_days
        out.append(merged)
    log.info(
        "filter_lifespan",
        n_in=len(rows),
        n_out=len(out),
        n_dropped_missing_dates=dropped_missing,
    )
    return out, dropped_missing


def filter_cardinality(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_event[r.get("event_ticker", "")].append(r)
    out = []
    for evt, members in by_event.items():
        n = len(members)
        if n >= 5:
            continue
        if n == 1:
            structure = "single-binary"
        else:
            structure = "multi-outcome-2-4"
        for r in members:
            merged = dict(r)
            merged["structure"] = structure
            out.append(merged)
    log.info(
        "filter_cardinality",
        n_in=len(rows),
        n_out=len(out),
        n_events_in=len(by_event),
    )
    return out


# ---------------------------------------------------------------------------
# Reachability probe
# ---------------------------------------------------------------------------


def probe_one_market(
    client: httpx.Client,
    ticker: str,
    series: str,
    open_time_iso: str,
    expected_settlement_iso: str,
) -> tuple[str, int | None]:
    """Returns ('ok'|'404'|'empty'|'other', candle_count_or_None).

    Single GET to /series/{series}/markets/{ticker}/candlesticks with
    period_interval=1440. Exponential backoff on 429 per BACKOFF_SCHEDULE.
    """
    open_dt = _parse_iso(open_time_iso)
    end_dt = _parse_iso(expected_settlement_iso)
    if open_dt is None or end_dt is None:
        log.warning("probe_skipped_bad_dates", ticker=ticker)
        return ("other", None)

    start_ts = int((open_dt - timedelta(days=1)).timestamp())
    end_ts = int((end_dt + timedelta(days=1)).timestamp())
    url = f"{KALSHI_API_BASE}/series/{series}/markets/{ticker}/candlesticks"
    params = {
        "period_interval": 1440,
        "start_ts": start_ts,
        "end_ts": end_ts,
    }

    for attempt, backoff_seconds in enumerate([0, *BACKOFF_SCHEDULE]):
        if backoff_seconds:
            log.info(
                "probe_backoff",
                ticker=ticker,
                attempt=attempt,
                sleep_s=backoff_seconds,
            )
            time.sleep(backoff_seconds)
        try:
            r = client.get(url, params=params)
        except httpx.HTTPError as e:
            log.warning("probe_http_error", ticker=ticker, error=str(e))
            return ("other", None)

        if r.status_code == 429:
            continue
        if r.status_code == 404:
            return ("404", None)
        if r.status_code != 200:
            log.warning(
                "probe_unexpected_status",
                ticker=ticker,
                status=r.status_code,
                body_preview=r.text[:200],
            )
            return ("other", None)

        try:
            data = r.json()
        except ValueError:
            log.warning("probe_non_json_200", ticker=ticker)
            return ("other", None)
        candles = data.get("candlesticks") or []
        if not candles:
            return ("empty", 0)
        return ("ok", len(candles))

    # exhausted retries on 429
    log.warning("probe_429_exhausted", ticker=ticker)
    return ("other", None)


def filter_reachability(rows: list[dict[str, str]], funnel: Funnel) -> list[dict[str, str]]:
    out = []
    funnel.reach_attempted = len(rows)
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        for i, r in enumerate(rows, 1):
            ticker = r["ticker"]
            series = r["series_ticker"]
            outcome, count = probe_one_market(
                client,
                ticker,
                series,
                r["open_time"],
                r["expected_settlement_time"],
            )
            if outcome == "ok":
                funnel.reach_ok += 1
                merged = dict(r)
                merged["candle_count"] = count
                merged["reachable"] = "true"
                out.append(merged)
            elif outcome == "404":
                funnel.reach_404 += 1
            elif outcome == "empty":
                funnel.reach_empty += 1
            else:
                funnel.reach_other += 1

            if i % 10 == 0:
                log.info(
                    "probe_progress",
                    completed=i,
                    total=len(rows),
                    reachable_so_far=funnel.reach_ok,
                )
            time.sleep(RATE_LIMIT_SLEEP_SECONDS)

    log.info(
        "filter_reachability_done",
        n_in=len(rows),
        n_out=len(out),
        reach_404=funnel.reach_404,
        reach_empty=funnel.reach_empty,
        reach_other=funnel.reach_other,
    )
    return out


# ---------------------------------------------------------------------------
# Effective window population (per notes/universe-construction.md §2)
# ---------------------------------------------------------------------------


def populate_effective_windows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """v0.1: empty-schedule scheduled-event filter.

    effective_window_start = open_time
    effective_window_end   = expected_settlement_time
    """
    for r in rows:
        r["effective_window_start"] = r["open_time"]
        r["effective_window_end"] = r["expected_settlement_time"]
    return rows


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------


def write_output(rows: list[dict[str, str]]) -> None:
    rows_sorted = sorted(rows, key=lambda r: r["ticker"])
    with OUTPUT_TEST_B_UNIVERSE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows_sorted:
            writer.writerow({col: r.get(col, "") for col in OUTPUT_COLUMNS})
    log.info("wrote_output", n=len(rows_sorted), path=str(OUTPUT_TEST_B_UNIVERSE))


# ---------------------------------------------------------------------------
# Funnel printer (per notes/universe-construction.md §6)
# ---------------------------------------------------------------------------


def print_funnel(funnel: Funnel) -> None:
    print()
    print("==================================================================")
    print("Test B universe construction — funnel diagnostics")
    print(f"Universe lockdate: {UNIVERSE_LOCKDATE}")
    print("==================================================================")
    print(f"candidate-universe.csv rows                     = {funnel.raw_input:>5}")
    print(f"  after status=finalized                        = {funnel.after_finalized:>5}")
    print(f"  after series in bucket-assignments            = {funnel.after_series_join:>5}")
    print(f"  after walkthrough_excluded=false              = {funnel.after_walkthrough_drop:>5}")
    print(f"  after out_of_scope_excluded=false             = {funnel.after_oos_drop:>5}")
    print( "  (assertion: primary_bucket non-empty for all)")
    print(f"  after lifespan >= 30 days                     = {funnel.after_lifespan:>5}")
    print(f"  after multi-outcome cardinality (drop 5+)     = {funnel.after_cardinality:>5}")
    print(f"  after reachability probe (drop 404/empty)     = {funnel.after_reachability:>5}")
    print(f"final test-b-universe.csv rows                  = {funnel.after_reachability:>5}")
    print()
    print("structure breakdown (final):")
    for s in ("single-binary", "multi-outcome-2-4"):
        print(f"  {s:<45} = {funnel.structure_counts.get(s, 0):>5}")
    print()
    print("bucket breakdown (final):")
    for b in (
        "macro",
        "geopolitics",
        "us_politics",
        "us_political_appointment",
        "policy_outcome_quantitative",
    ):
        print(f"  {b:<45} = {funnel.bucket_counts.get(b, 0):>5}")
    print()
    print("reachability diagnostic:")
    print(f"  attempted probes                              = {funnel.reach_attempted:>5}")
    print(f"  reachable (HTTP 200 with non-empty candles)   = {funnel.reach_ok:>5}")
    print(f"  unreachable (HTTP 404)                        = {funnel.reach_404:>5}")
    print(f"  unreachable (HTTP 200 empty array)            = {funnel.reach_empty:>5}")
    print(f"  unreachable (other / 429-after-retries)       = {funnel.reach_other:>5}")
    print("==================================================================")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    funnel = Funnel()

    raw_rows = load_candidate_universe()
    bucket_map = load_series_buckets()
    funnel.raw_input = len(raw_rows)

    rows = filter_finalized(raw_rows)
    funnel.after_finalized = len(rows)

    rows = filter_series_join(rows, bucket_map)
    funnel.after_series_join = len(rows)

    rows = filter_drop_walkthrough(rows)
    funnel.after_walkthrough_drop = len(rows)

    rows = filter_drop_out_of_scope(rows)
    funnel.after_oos_drop = len(rows)

    assert_primary_bucket_populated(rows)

    rows, _dropped_missing = filter_lifespan(rows)
    funnel.after_lifespan = len(rows)

    rows = filter_cardinality(rows)
    funnel.after_cardinality = len(rows)

    rows = filter_reachability(rows, funnel)
    funnel.after_reachability = len(rows)

    rows = populate_effective_windows(rows)

    funnel.structure_counts = dict(Counter(r["structure"] for r in rows))
    funnel.bucket_counts = dict(Counter(r["primary_bucket"] for r in rows))

    write_output(rows)
    print_funnel(funnel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
