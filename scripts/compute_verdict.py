"""Stage 3b verdict computation — applies the locked criteria in
notes/stage-3b-preregistration.md to notes/test-b-positions.csv and
emits the verdict numbers.

Inputs (hardcoded paths):
- notes/test-b-positions.csv         (Stage 2b output, locked at 13b048b)
- reference-data/dgs3mo.csv          (Stage 3b commit 1 of 3, 1205656)

Outputs (hardcoded paths):
- notes/test-b-verdict-numbers.json  (canonical structured output)
- notes/test-b-verdict-numbers.txt   (human-readable summary,
                                      same content as stdout)

Idempotent: rerun produces bit-identical outputs (bootstrap seed
pinned at 42 per stage-3b-preregistration §15). No timestamps in
output.

Computations follow notes/stage-3b-preregistration.md §§9-15:
- Per-position excess return = annualized_return - DGS3MO_at_fill
  (DGS3MO from FRED, percent-to-decimal, forward-filled per §9)
- Per-cell: fill_rate, median_excess, mean_excess,
  bootstrap SE of median (10000 resamples, seed=42, §10),
  median_raw (Option 1 sensitivity), voided_count
- Pass conditions per §6.6 (= preregistration §5)
- Verdict ladder per §6.9 with INSUFFICIENT-SAMPLE-dominates
  disambiguation locked in this commit's planning chat
"""
from __future__ import annotations

import csv
import json
import random
import statistics
import sys
import textwrap
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.tbill import tbill_rate  # forward-fill lookup per §9

POSITIONS_CSV = PROJECT_ROOT / "notes" / "test-b-positions.csv"
DGS3MO_CSV = PROJECT_ROOT / "reference-data" / "dgs3mo.csv"
OUTPUT_JSON = PROJECT_ROOT / "notes" / "test-b-verdict-numbers.json"
OUTPUT_TXT = PROJECT_ROOT / "notes" / "test-b-verdict-numbers.txt"

# Locked refs to upstream artifacts (per Stage 3b commit chain)
PREREGISTRATION_COMMIT = "488fbb4"
POSITIONS_CSV_COMMIT = "13b048b"
DGS3MO_CSV_COMMIT = "1205656"

# Locked parameters from preregistration
BOOTSTRAP_RESAMPLES = 10000  # §10
BOOTSTRAP_SEED = 42          # §15
THRESHOLD_FILL_RATE = 0.30   # §5 condition 2
THRESHOLD_N_FILLED = 30      # §5 condition 4
THRESHOLD_MEDIAN_EXCESS = 0.0  # §5 condition 1 (Option 2 binding)


# ---------------------------------------------------------------------------
# DGS3MO loading (for window mean — per-position lookup uses simulator.tbill)
# ---------------------------------------------------------------------------


def load_dgs3mo_observations() -> list[tuple[date, float]]:
    """Return all (date, value_decimal) pairs from the CSV where value
    is non-empty. Used for the §9 window-mean sensitivity reference;
    per-position lookup goes through simulator.tbill.tbill_rate which
    forward-fills."""
    out: list[tuple[date, float]] = []
    with DGS3MO_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            d_str = (r.get("observation_date") or "").strip()
            v_str = (r.get("DGS3MO") or "").strip()
            if not d_str or not v_str:
                continue
            d = date.fromisoformat(d_str)
            # FRED publishes percent (e.g., 3.68); convert to decimal
            value = float(v_str) / 100.0
            out.append((d, value))
    out.sort(key=lambda x: x[0])
    return out


def window_mean_dgs3mo(
    observations: list[tuple[date, float]],
    start: date,
    end: date,
) -> float:
    """Mean of DGS3MO observed values within [start, end] inclusive.
    Uses only actual published rates (not forward-filled values),
    consistent with 'mean(DGS3MO over the test window)' as a
    literal series mean."""
    in_window = [v for d, v in observations if start <= d <= end]
    if not in_window:
        return 0.0
    return statistics.mean(in_window)


# ---------------------------------------------------------------------------
# Per-cell computations
# ---------------------------------------------------------------------------


def bootstrap_se_median(values: list[float]) -> float | None:
    """Bootstrap SE of the median per §10: 10000 resamples with
    replacement, seed 42, computed on the filled-row excess-return
    distribution. Returns None when n<2 (bootstrap ill-defined per
    the planning chat: 'cannot pass condition 3 without a valid SE')."""
    n = len(values)
    if n < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    medians = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        resample = rng.choices(values, k=n)
        medians.append(statistics.median(resample))
    return statistics.stdev(medians)


def cell_metrics(
    cell_attempted: list[dict],
    cell_filled: list[dict],
    tbill_window_avg: float,
) -> dict:
    """Compute all per-cell metrics. cell_filled rows have `_excess`
    and `_annualized_return_float` keys pre-populated by main."""
    n_attempted = len(cell_attempted)
    n_filled = len(cell_filled)
    fill_rate = (n_filled / n_attempted) if n_attempted > 0 else 0.0

    if n_filled == 0:
        return {
            "n_attempted": n_attempted,
            "n_filled": 0,
            "fill_rate": fill_rate,
            "median_excess": None,
            "mean_excess": None,
            "se_median_excess": None,
            "median_raw": None,
            "voided_count": 0,
            "voided_fraction": 0.0,
            "condition_1_median_excess_geq_0": False,
            "condition_2_fill_rate_geq_30pct": fill_rate >= THRESHOLD_FILL_RATE,
            "condition_3_mean_vs_median_passes": False,
            "condition_4_n_filled_geq_30": False,
            "all_4_pass": False,
            "option1_passes": False,
        }

    excess_values = [r["_excess"] for r in cell_filled]
    raw_values = [r["_annualized_return_float"] for r in cell_filled]

    median_excess = statistics.median(excess_values)
    mean_excess = statistics.mean(excess_values)
    se_median = bootstrap_se_median(excess_values)
    median_raw = statistics.median(raw_values)

    voided_count = sum(
        1 for r in cell_filled if r.get("settlement_outcome") == "voided"
    )
    voided_fraction = voided_count / n_filled

    condition_1 = median_excess >= THRESHOLD_MEDIAN_EXCESS
    condition_2 = fill_rate >= THRESHOLD_FILL_RATE
    if se_median is None:
        condition_3 = False  # cannot pass condition 3 without valid SE
    else:
        condition_3 = mean_excess >= (median_excess - 2 * se_median)
    condition_4 = n_filled >= THRESHOLD_N_FILLED
    all_4_pass = bool(condition_1 and condition_2 and condition_3 and condition_4)

    option1_passes = median_raw >= tbill_window_avg

    return {
        "n_attempted": n_attempted,
        "n_filled": n_filled,
        "fill_rate": fill_rate,
        "median_excess": median_excess,
        "mean_excess": mean_excess,
        "se_median_excess": se_median,
        "median_raw": median_raw,
        "voided_count": voided_count,
        "voided_fraction": voided_fraction,
        "condition_1_median_excess_geq_0": bool(condition_1),
        "condition_2_fill_rate_geq_30pct": bool(condition_2),
        "condition_3_mean_vs_median_passes": bool(condition_3),
        "condition_4_n_filled_geq_30": bool(condition_4),
        "all_4_pass": all_4_pass,
        "option1_passes": bool(option1_passes),
    }


# ---------------------------------------------------------------------------
# Verdict ladder
# ---------------------------------------------------------------------------


# Verdict ladder ordering (per stage-3b-preregistration §6.9 +
# 3b.2 planning chat): INSUFFICIENT SAMPLE is determined first
# based on primary cell N<30, dominating regardless of
# descriptive cells. §6.9's "no PASS/FAIL verdict" language
# for INSUFFICIENT SAMPLE is the load-bearing tell: when
# primary N<30, neither PASS nor DESCRIPTIVE PASS is on the
# table. Descriptive-cell findings are still reported as
# context but do not promote to DESCRIPTIVE PASS when primary
# N<30. This disambiguates a spec ambiguity in §6.9 where
# INSUFFICIENT SAMPLE and DESCRIPTIVE PASS could overlap.
def determine_verdict(cells: dict, primary_key: str) -> tuple[str, str, list[str]]:
    """Apply the verdict ladder. Returns (verdict, reasoning, passing_descriptive_cells)."""
    primary = cells[primary_key]

    if primary["n_filled"] < THRESHOLD_N_FILLED:
        verdict = "INSUFFICIENT SAMPLE"
        reasoning = (
            f"Primary cell ({primary['track']} × {primary['structure']}) has "
            f"n_filled={primary['n_filled']} < {THRESHOLD_N_FILLED}. Per the "
            f"verdict-ladder ordering locked in stage-3b-preregistration §§6.9 "
            f"and the planning chat, INSUFFICIENT SAMPLE dominates: "
            f"descriptive-cell findings are reported but do not promote to "
            f"DESCRIPTIVE PASS when primary N<30."
        )
        return verdict, reasoning, []

    if primary["all_4_pass"]:
        verdict = "PASS"
        reasoning = (
            f"Primary cell ({primary['track']} × {primary['structure']}) "
            f"satisfies all 4 conditions in §6.6. Phase 1b paper-shadow on "
            f"primary-cell parameters per §6.9 PASS path."
        )
        return verdict, reasoning, []

    # Primary N>=30 but doesn't pass all 4. Check descriptive cells.
    passing_descriptive = sorted([
        k for k, v in cells.items()
        if not v["is_primary"] and v["all_4_pass"]
    ])

    if passing_descriptive:
        verdict = "DESCRIPTIVE PASS"
        reasoning = (
            f"Primary cell does not pass; descriptive cells that meet all "
            f"4 conditions: {', '.join(passing_descriptive)}. Phase 1b "
            f"paper-shadow runs on the closest-passing descriptive cell. "
            f"v0.1 in the primary-cell-defined form is abandoned per §6.9 "
            f"DESCRIPTIVE PASS path."
        )
        return verdict, reasoning, passing_descriptive

    verdict = "FAIL"
    reasoning = (
        f"No cell (primary or descriptive) meets all 4 conditions in §6.6. "
        f"v0.1 abandoned. Per §6.9, the thesis itself is reviewed only if "
        f"Track 1 (full strategy) also returns ≤ 0%."
    )
    return verdict, reasoning, []


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def load_positions() -> list[dict[str, str]]:
    with POSITIONS_CSV.open(newline="") as f:
        return list(csv.DictReader(f))


def cell_filter(rows: list[dict], track: str, structure: str) -> list[dict]:
    """Track 1 = all rows; Track 2 = side='buy-YES'; Track 3 = side='sell-YES'."""
    out = rows
    if track == "track2":
        out = [r for r in out if r["side"] == "buy-YES"]
    elif track == "track3":
        out = [r for r in out if r["side"] == "sell-YES"]
    out = [r for r in out if r["structure"] == structure]
    return out


def main() -> int:
    rows = load_positions()

    # Compute per-position fields needed for metrics: fill_date,
    # DGS3MO_at_fill, excess_return (filled rows only)
    filled_rows = []
    for r in rows:
        if r["outcome"] != "filled":
            continue
        fd = date.fromisoformat(r["fill_date"])
        # tbill_rate handles forward-fill per §9
        tbill_decimal = float(tbill_rate(fd, csv_path=DGS3MO_CSV))
        ar_float = float(r["annualized_return"])
        r["_fill_date_obj"] = fd
        r["_tbill_at_fill"] = tbill_decimal
        r["_annualized_return_float"] = ar_float
        r["_excess"] = ar_float - tbill_decimal
        filled_rows.append(r)

    if not filled_rows:
        # Defensive — but Stage 2b output had 102 filled, so this won't fire
        sys.stderr.write("ERROR: no filled rows in positions CSV\n")
        return 1

    # Test window for Option 1 sensitivity: from min(fill_date) to
    # max(fill_date) across all filled positions in the universe.
    fill_dates = [r["_fill_date_obj"] for r in filled_rows]
    test_window_start = min(fill_dates)
    test_window_end = max(fill_dates)

    dgs3mo_observations = load_dgs3mo_observations()
    tbill_window_avg = window_mean_dgs3mo(
        dgs3mo_observations, test_window_start, test_window_end
    )

    # Compute the 6 cells
    cells: dict[str, dict] = {}
    track_label = {
        "track1": "Track 1 (full)",
        "track2": "Track 2 (favorite-buy)",
        "track3": "Track 3 (longshot-sell)",
    }
    for track in ("track2", "track3", "track1"):  # primary first; track1 last
        for structure in ("single-binary", "multi-outcome-2-4"):
            attempted = cell_filter(rows, track, structure)
            filled = cell_filter(filled_rows, track, structure)
            metrics = cell_metrics(attempted, filled, tbill_window_avg)
            metrics["track"] = track_label[track]
            metrics["structure"] = structure
            metrics["is_primary"] = (track == "track2" and structure == "single-binary")
            key = f"{track}_{structure}"
            cells[key] = metrics

    # Track 1 transparency check (§6.7): operates on Track 1 combined
    # (= all filled rows, both structures combined). NOT one of the 6
    # cells; a separate computation per the spec.
    track1_combined_n = len(filled_rows)
    if track1_combined_n > 0:
        t1_excess = [r["_excess"] for r in filled_rows]
        track1_median = statistics.median(t1_excess)
        track1_mean = statistics.mean(t1_excess)
    else:
        track1_median = None
        track1_mean = None

    primary_key = "track2_single-binary"
    primary_passes = cells[primary_key]["all_4_pass"]
    track1_transparency_flag = bool(
        primary_passes
        and track1_median is not None
        and track1_median <= 0
    )

    # Verdict ladder
    verdict, verdict_reasoning, passing_descriptive = determine_verdict(
        cells, primary_key
    )

    # Build canonical output dict (insertion order = schema order)
    output = {
        "metadata": {
            "preregistration_commit": PREREGISTRATION_COMMIT,
            "positions_csv_commit": POSITIONS_CSV_COMMIT,
            "dgs3mo_csv_commit": DGS3MO_CSV_COMMIT,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "n_total_attempted": len(rows),
            "n_total_filled": len(filled_rows),
        },
        "tbill_sensitivity": {
            "tbill_window_avg": tbill_window_avg,
            "tbill_window_start": test_window_start.isoformat(),
            "tbill_window_end": test_window_end.isoformat(),
        },
        "cells": cells,
        "track1_transparency": {
            "track1_combined_n_filled": track1_combined_n,
            "track1_combined_median_excess": track1_median,
            "track1_combined_mean_excess": track1_mean,
            "transparency_flag": track1_transparency_flag,
        },
        "verdict": {
            "verdict": verdict,
            "reasoning": verdict_reasoning,
            "passing_descriptive_cells": passing_descriptive,
        },
    }

    # Write JSON (sort_keys=False preserves insertion order)
    with OUTPUT_JSON.open("w") as f:
        json.dump(output, f, indent=2, sort_keys=False)
        f.write("\n")  # trailing newline for unix-friendliness

    # Write text summary (also printed to stdout)
    text = build_text_summary(output)
    OUTPUT_TXT.write_text(text)
    print(text, end="")

    return 0


# ---------------------------------------------------------------------------
# Text summary rendering
# ---------------------------------------------------------------------------


def _fmt_opt(v: float | None, fmt: str = "{:.6f}") -> str:
    return "(insufficient data)" if v is None else fmt.format(v)


def build_text_summary(output: dict) -> str:
    sep = "=" * 72
    lines: list[str] = []
    lines.append(sep)
    lines.append("Stage 3b verdict computation — numbers")
    lines.append(sep)
    lines.append("")

    md = output["metadata"]
    lines.append("Inputs:")
    lines.append(f"  pre-registration commit:  {md['preregistration_commit']}")
    lines.append(f"  positions CSV commit:     {md['positions_csv_commit']}")
    lines.append(f"  DGS3MO CSV commit:        {md['dgs3mo_csv_commit']}")
    lines.append(f"  bootstrap seed:           {md['bootstrap_seed']}")
    lines.append(f"  bootstrap resamples:      {md['bootstrap_resamples']}")
    lines.append(f"  n_total_attempted:        {md['n_total_attempted']}")
    lines.append(f"  n_total_filled:           {md['n_total_filled']}")
    lines.append("")

    sens = output["tbill_sensitivity"]
    lines.append("T-bill window mean (Option 1 sensitivity reference):")
    lines.append(
        f"  window: {sens['tbill_window_start']} to {sens['tbill_window_end']}"
    )
    lines.append(
        f"  mean DGS3MO over window:  {sens['tbill_window_avg']:.6f} "
        f"({sens['tbill_window_avg']*100:.4f}%)"
    )
    lines.append("")

    lines.append(sep)
    lines.append("Per-cell metrics")
    lines.append(sep)

    # Primary first, then descriptive
    cell_order = [
        ("track2_single-binary", "PRIMARY"),
        ("track2_multi-outcome-2-4", "descriptive"),
        ("track3_single-binary", "descriptive"),
        ("track3_multi-outcome-2-4", "descriptive"),
        ("track1_single-binary", "descriptive"),
        ("track1_multi-outcome-2-4", "descriptive"),
    ]
    for key, label in cell_order:
        c = output["cells"][key]
        lines.append("")
        lines.append(f"--- {c['track']} × {c['structure']}  [{label}] ---")
        lines.append(f"  n_attempted:         {c['n_attempted']}")
        lines.append(f"  n_filled:            {c['n_filled']}")
        lines.append(
            f"  fill_rate:           {c['fill_rate']:.4f} "
            f"({c['fill_rate']*100:.2f}%)"
        )
        lines.append(f"  median_excess:       {_fmt_opt(c['median_excess'])}")
        lines.append(f"  mean_excess:         {_fmt_opt(c['mean_excess'])}")
        se_str = (
            "(n<2, undefined)"
            if c["se_median_excess"] is None
            else f"{c['se_median_excess']:.6f}"
        )
        lines.append(f"  se_median_excess:    {se_str}")
        lines.append(
            f"  median_raw:          {_fmt_opt(c['median_raw'])}  "
            f"(Option 1 sensitivity)"
        )
        lines.append(
            f"  voided_count:        {c['voided_count']} "
            f"({c['voided_fraction']*100:.2f}%)"
        )
        lines.append("  Conditions:")
        lines.append(
            f"    1. median_excess >= 0:        "
            f"{c['condition_1_median_excess_geq_0']}"
        )
        lines.append(
            f"    2. fill_rate >= 30%:          "
            f"{c['condition_2_fill_rate_geq_30pct']}"
        )
        lines.append(
            f"    3. mean >= median - 2*SE:     "
            f"{c['condition_3_mean_vs_median_passes']}"
        )
        lines.append(
            f"    4. n_filled >= 30:            "
            f"{c['condition_4_n_filled_geq_30']}"
        )
        lines.append(f"  ALL 4 PASS:          {c['all_4_pass']}")
        lines.append(
            f"  Option 1 (median_raw >= window_avg):  {c['option1_passes']}"
        )

    lines.append("")
    lines.append(sep)
    lines.append("Track 1 transparency check (§6.7)")
    lines.append(sep)
    t1 = output["track1_transparency"]
    if t1["track1_combined_median_excess"] is None:
        lines.append("  Track 1 combined: insufficient filled positions")
    else:
        lines.append(f"  Track 1 combined n_filled:  {t1['track1_combined_n_filled']}")
        lines.append(
            f"  Track 1 combined median excess: "
            f"{t1['track1_combined_median_excess']:.6f}"
        )
        lines.append(
            f"  Track 1 combined mean excess:   "
            f"{t1['track1_combined_mean_excess']:.6f}"
        )
    lines.append(
        f"  Transparency flag (primary PASS AND Track 1 median<=0): "
        f"{t1['transparency_flag']}"
    )
    lines.append("")

    lines.append(sep)
    lines.append("VERDICT")
    lines.append(sep)
    v = output["verdict"]
    lines.append(f"  {v['verdict']}")
    lines.append("")
    for line in textwrap.wrap(v["reasoning"], width=70):
        lines.append(f"  {line}")
    if v["passing_descriptive_cells"]:
        lines.append("")
        lines.append(
            f"  Passing descriptive cells: "
            f"{', '.join(v['passing_descriptive_cells'])}"
        )
    lines.append("")
    lines.append(sep)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
