"""Output writer for the Test B simulator.

Two writer functions:
- write_positions_csv(positions, path) — the locked 23-column
  positions table per notes/simulator-design.md §4.
- write_diagnostics(positions, path) — the diagnostics block per
  notes/simulator-design.md §5: funnel, per-bucket and per-structure
  fill rates, capital utilization, voided count.

Decimal formatting convention (chosen for v0.1 — applied consistently):
- `limit_price` and `fill_price` quantized to 4 decimal places per
  the §4 schema's explicit "_dollars" convention (e.g., "0.1000").
- All other Decimal fields formatted via `str(Decimal)`, preserving
  the full precision produced by the computation. This makes
  re-runs byte-identical given identical inputs (Decimal -> str is
  deterministic and round-trip safe) and lets the downstream
  analysis stage read full precision without re-parsing.
- None values render as empty CSV cells.
- Dates render in YYYY-MM-DD via date.isoformat().

Sort order: (ticker, post_date) ascending. UTF-8 encoding, LF line
endings.

Capital-utilization computation: walks the test period day-by-day
from earliest post_date to latest settlement_date. A filled
position's capital is "active" at day D iff fill_date < D <=
settlement_date (matches simulator/cap_layer.py's
_capital_active_at semantics). Reports peak, mean, and the count
of daily-check days at or above the $30K cap.
"""
from __future__ import annotations

import csv
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from simulator.cap_layer import CAPITAL_CAP, OUTCOME_BLOCKED_BY_CAP
from simulator.daily_check import (
    OUTCOME_FILLED,
    OUTCOME_OUT_OF_ZONE,
    OUTCOME_STALE,
    PostEvent,
)

# Locked column order from notes/simulator-design.md §4
COLUMN_ORDER: tuple[str, ...] = (
    "ticker",
    "event_ticker",
    "series_ticker",
    "primary_bucket",
    "structure",
    "post_date",
    "side",
    "limit_price",
    "contracts_attempted",
    "capital_deployed",
    "outcome",
    "fill_date",
    "fill_price",
    "total_fees",
    "settlement_date",
    "settlement_outcome",
    "settlement_value_per_contract",
    "position_pnl",
    "position_pnl_net_fees",
    "position_return",
    "holding_period_days",
    "annualized_return",
    "tbill_rate_at_fill",
)

# Locked thesis buckets — 5 rows in the per-bucket diagnostic
BUCKETS: tuple[str, ...] = (
    "macro",
    "geopolitics",
    "us_politics",
    "us_political_appointment",
    "policy_outcome_quantitative",
)

# Locked structure values — 2 rows in the per-structure diagnostic
STRUCTURES: tuple[str, ...] = ("single-binary", "multi-outcome-2-4")

# Funnel outcomes the diagnostic reports
FUNNEL_OUTCOMES: tuple[str, ...] = (
    OUTCOME_FILLED,
    OUTCOME_STALE,
    OUTCOME_OUT_OF_ZONE,
    OUTCOME_BLOCKED_BY_CAP,
    "blocked_by_filter",  # defensive — sub-stage 2b.7 may emit this
)

# Fields whose limit-price-style 4-dp formatting is explicit per §4
_FOUR_DP_FIELDS = frozenset({"limit_price", "fill_price"})


# ---------------------------------------------------------------------------
# Value formatting
# ---------------------------------------------------------------------------


def _format_value(value: Any, column_name: str) -> str:
    """Render one cell as a string for CSV writing. None -> empty.
    Decimals: 4 dp for limit_price / fill_price; str(Decimal) for the
    rest. Dates: ISO. Other types: str()."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        if column_name in _FOUR_DP_FIELDS:
            return str(value.quantize(Decimal("0.0001")))
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


def write_positions_csv(positions: Iterable[PostEvent], path: Path) -> int:
    """Write `positions` to `path` as the locked 23-column CSV.

    Sort order: (ticker, post_date) ascending. UTF-8, LF line endings.

    Returns the number of data rows written (excluding the header).
    Re-runs with identical input produce byte-identical output.
    """
    rows = sorted(positions, key=lambda e: (e.ticker, e.post_date))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(COLUMN_ORDER)
        for ev in rows:
            writer.writerow([_format_value(getattr(ev, c), c) for c in COLUMN_ORDER])
    return len(rows)


# ---------------------------------------------------------------------------
# Diagnostic computations
# ---------------------------------------------------------------------------


def _funnel_counts(positions: Iterable[PostEvent]) -> dict[str, int]:
    """Return outcome counts. Outcomes not in FUNNEL_OUTCOMES still
    appear in 'other' so we don't silently drop unexpected values."""
    by_outcome = Counter(p.outcome for p in positions)
    counts: dict[str, int] = {o: by_outcome.get(o, 0) for o in FUNNEL_OUTCOMES}
    counts["other"] = sum(
        v for k, v in by_outcome.items() if k not in FUNNEL_OUTCOMES
    )
    counts["total_attempted"] = sum(by_outcome.values())
    return counts


def _per_group_fill_rate(
    positions: Iterable[PostEvent],
    attr: str,
    group_keys: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    """For each group_key in group_keys, compute attempted / filled /
    fill_rate."""
    by_group_attempted: Counter[str] = Counter()
    by_group_filled: Counter[str] = Counter()
    for p in positions:
        key = getattr(p, attr)
        by_group_attempted[key] += 1
        if p.outcome == OUTCOME_FILLED:
            by_group_filled[key] += 1
    out: dict[str, dict[str, Any]] = {}
    for key in group_keys:
        attempted = by_group_attempted.get(key, 0)
        filled = by_group_filled.get(key, 0)
        rate = (filled / attempted) if attempted > 0 else 0.0
        out[key] = {
            "attempted": attempted,
            "filled": filled,
            "fill_rate_fraction": filled / attempted if attempted > 0 else 0.0,
            "fill_rate_pct": rate * 100,
        }
    return out


def _capital_utilization(positions: Iterable[PostEvent]) -> dict[str, Any]:
    """Walk the test period day-by-day; report peak, mean, days at cap.

    Capital active at day D: fill_date < D <= settlement_date (matches
    simulator/cap_layer.py)."""
    filled = [p for p in positions if p.outcome == OUTCOME_FILLED
              and p.fill_date is not None and p.settlement_date is not None]
    if not filled:
        return {
            "peak_capital_deployed": Decimal("0"),
            "mean_capital_deployed": Decimal("0"),
            "days_at_or_above_cap": 0,
            "n_days": 0,
        }
    earliest = min(p.fill_date for p in filled)  # type: ignore[type-var]
    latest = max(p.settlement_date for p in filled)  # type: ignore[type-var]

    peak = Decimal("0")
    total = Decimal("0")
    days_at_cap = 0
    n_days = 0
    cur_day = earliest + timedelta(days=1)  # start at fill_date+1 (first active day)
    while cur_day <= latest:
        active = sum(
            (p.capital_deployed for p in filled
             if p.fill_date is not None and p.fill_date < cur_day
             and p.settlement_date is not None and cur_day <= p.settlement_date),
            start=Decimal("0"),
        )
        if active > peak:
            peak = active
        if active >= CAPITAL_CAP:
            days_at_cap += 1
        total += active
        n_days += 1
        cur_day += timedelta(days=1)

    mean = (total / Decimal(n_days)) if n_days > 0 else Decimal("0")
    return {
        "peak_capital_deployed": peak,
        "mean_capital_deployed": mean,
        "days_at_or_above_cap": days_at_cap,
        "n_days": n_days,
    }


def _voided_count(positions: Iterable[PostEvent]) -> int:
    return sum(1 for p in positions if p.settlement_outcome == "voided")


def compute_diagnostics(positions: Iterable[PostEvent]) -> dict[str, Any]:
    """Materialize all diagnostic numbers as a dict. Used both by
    write_diagnostics (for the text file) and by tests (which check
    the computations directly)."""
    positions_list = list(positions)
    return {
        "funnel": _funnel_counts(positions_list),
        "per_bucket": _per_group_fill_rate(positions_list, "primary_bucket", BUCKETS),
        "per_structure": _per_group_fill_rate(positions_list, "structure", STRUCTURES),
        "capital_utilization": _capital_utilization(positions_list),
        "voided_count": _voided_count(positions_list),
    }


# ---------------------------------------------------------------------------
# Diagnostics text writer
# ---------------------------------------------------------------------------


def _format_diagnostics_text(diag: dict[str, Any]) -> str:
    """Render the diagnostics dict as a human-readable text block.
    Matches the format used elsewhere (e.g.,
    notes/universe-construction.md §6 funnel)."""
    lines: list[str] = []
    sep = "=" * 66
    lines.append(sep)
    lines.append("Test B simulator diagnostics")
    lines.append(sep)

    funnel = diag["funnel"]
    lines.append("")
    lines.append("Funnel (counts of attempted posts):")
    lines.append(f"  total attempted                  = {funnel['total_attempted']:>5}")
    lines.append(f"  filled                           = {funnel[OUTCOME_FILLED]:>5}")
    lines.append(f"  stale_cancelled                  = {funnel[OUTCOME_STALE]:>5}")
    lines.append(f"  out_of_zone_cancelled            = {funnel[OUTCOME_OUT_OF_ZONE]:>5}")
    lines.append(f"  blocked_by_cap                   = {funnel[OUTCOME_BLOCKED_BY_CAP]:>5}")
    lines.append(f"  blocked_by_filter                = {funnel['blocked_by_filter']:>5}")
    if funnel["other"] > 0:
        lines.append(f"  other (unexpected outcomes)      = {funnel['other']:>5}")

    lines.append("")
    lines.append("Per-bucket fill rate:")
    for bucket in BUCKETS:
        info = diag["per_bucket"][bucket]
        rate_str = f"{info['fill_rate_pct']:.2f}%"
        frac_str = f"({info['filled']}/{info['attempted']})"
        lines.append(f"  {bucket:<32} {rate_str:>10} {frac_str}")

    lines.append("")
    lines.append("Per-structure fill rate:")
    for structure in STRUCTURES:
        info = diag["per_structure"][structure]
        rate_str = f"{info['fill_rate_pct']:.2f}%"
        frac_str = f"({info['filled']}/{info['attempted']})"
        lines.append(f"  {structure:<32} {rate_str:>10} {frac_str}")

    lines.append("")
    lines.append("Capital utilization:")
    util = diag["capital_utilization"]
    lines.append(f"  peak capital deployed            = ${util['peak_capital_deployed']}")
    lines.append(f"  mean capital deployed            = ${util['mean_capital_deployed']}")
    lines.append(f"  days at or above $30K cap        = {util['days_at_or_above_cap']:>5}")
    lines.append(f"  test-period day count            = {util['n_days']:>5}")

    lines.append("")
    lines.append(f"Voided positions: {diag['voided_count']}")
    lines.append(sep)
    return "\n".join(lines) + "\n"


def write_diagnostics(positions: Iterable[PostEvent], path: Path) -> dict[str, Any]:
    """Compute diagnostics and write the human-readable text file.
    Returns the diagnostics dict (so the caller can also print it
    to stdout per simulator-design.md §5)."""
    diag = compute_diagnostics(positions)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_format_diagnostics_text(diag))
    return diag
