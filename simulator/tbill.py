"""T-bill rate lookup for the Test B simulator.

Implements the per-fill-date 3-month Treasury Bill rate lookup specified
in `notes/simulator-design.md` §3.4. Reads the cached FRED DGS3MO CSV
(populated by `scripts/fetch_dgs3mo.py`), parses percentages to decimal
(5.27 -> 0.0527), and forward-fills weekends and federal holidays per
FRED's standard convention.

FRED CSV layout:
- Column 1: `observation_date` in YYYY-MM-DD
- Column 2: `DGS3MO` as percentage string (e.g., `5.27`) or empty
- Federal-holiday rows are present with empty rate column
- Weekend rows are simply absent

Forward-fill rule: for a queried date `d`, return the most recent prior
business-day rate at or before `d`. This handles both the weekend case
(no row at all) and the holiday case (row present but blank).

Out-of-range queries raise ValueError. Returned rates are
`decimal.Decimal`, not `float` — see simulator-design.md §3.4.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

CACHE_PATH = Path("/tmp/dgs3mo.csv")

# Module-level cache: maps csv_path -> sorted list of (date, Decimal rate)
# for rows with non-empty rate values. Holidays (empty cells) are excluded
# at parse time so forward-fill is a simple "latest date <= d" lookup.
_loaded_cache: dict[Path, list[tuple[date, Decimal]]] = {}


def _load(csv_path: Path) -> list[tuple[date, Decimal]]:
    """Load and sort (date, rate) rows from the FRED CSV. Cached per path."""
    if csv_path in _loaded_cache:
        return _loaded_cache[csv_path]
    rows: list[tuple[date, Decimal]] = []
    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            d_str = r.get("observation_date") or ""
            v_str = (r.get("DGS3MO") or "").strip()
            if not d_str or not v_str:
                continue
            try:
                d = datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            try:
                pct = Decimal(v_str)
            except Exception:
                continue
            # FRED publishes the value as a percentage (e.g. 5.27 = 5.27%).
            # Convert to decimal: 5.27 / 100 = 0.0527.
            rate = pct / Decimal("100")
            rows.append((d, rate))
    rows.sort(key=lambda x: x[0])
    _loaded_cache[csv_path] = rows
    return rows


def tbill_rate(d: date, csv_path: Path = CACHE_PATH) -> Decimal:
    """Return the FRED DGS3MO rate at date `d` as a Decimal.

    Forward-fills weekends and federal holidays. Raises ValueError if
    `d` is before the earliest row in the cache or after the latest.
    """
    rows = _load(csv_path)
    if not rows:
        raise ValueError(f"no rate data found in {csv_path}")
    earliest = rows[0][0]
    latest = rows[-1][0]
    if d < earliest:
        raise ValueError(
            f"requested date {d.isoformat()} is before the earliest "
            f"cached FRED date {earliest.isoformat()}"
        )
    if d > latest:
        raise ValueError(
            f"requested date {d.isoformat()} is after the latest "
            f"cached FRED date {latest.isoformat()}"
        )
    # Binary search for latest entry with date <= d.
    lo, hi = 0, len(rows) - 1
    found_idx = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if rows[mid][0] <= d:
            found_idx = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if found_idx < 0:
        raise ValueError(
            f"forward-fill failed for date {d.isoformat()}; cache may be "
            f"corrupt"
        )
    return rows[found_idx][1]
