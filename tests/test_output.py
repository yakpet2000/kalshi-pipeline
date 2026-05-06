"""Unit tests for simulator.output — CSV writer + diagnostics.

Synthetic position lists; no live data. Each test exercises one
required branch from the 2b.6 sub-stage plan.

Coverage:
- Column order matches simulator-design.md §4 exactly (23 columns)
- Voided rows have correct empty cells per §4 final paragraph
- blocked_by_cap rows have correct empty cells
- stale_cancelled / out_of_zone_cancelled rows have correct empty cells
- Re-run with same input produces byte-identical output
- Diagnostics math: funnel sums, per-bucket = 5 rows, per-structure
  = 2 rows, peak >= mean, voided count is non-negative integer
"""
from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from simulator.cap_layer import OUTCOME_BLOCKED_BY_CAP
from simulator.daily_check import (
    OUTCOME_FILLED,
    OUTCOME_OUT_OF_ZONE,
    OUTCOME_STALE,
    SIDE_BUY_YES,
    SIDE_SELL_YES,
    PostEvent,
)
from simulator.output import (
    BUCKETS,
    COLUMN_ORDER,
    STRUCTURES,
    compute_diagnostics,
    write_diagnostics,
    write_positions_csv,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _filled(
    ticker: str = "T-FILLED",
    *,
    bucket: str = "macro",
    structure: str = "single-binary",
    L: str = "0.10",
    fill_date: date = date(2026, 3, 1),
    settlement_date: date = date(2026, 3, 30),
    settlement_outcome: str = "no",
    capital: str = "1000.00",
) -> PostEvent:
    contracts = 10000
    return PostEvent(
        ticker=ticker,
        event_ticker=f"{ticker}-EVT",
        series_ticker="SERIES",
        primary_bucket=bucket,
        structure=structure,
        post_date=fill_date,
        side=SIDE_SELL_YES,
        limit_price=Decimal(L),
        contracts_attempted=contracts,
        capital_deployed=Decimal(capital),
        outcome=OUTCOME_FILLED,
        fill_date=fill_date,
        fill_price=Decimal(L),
        total_fees=Decimal("15.75"),
        settlement_date=settlement_date,
        settlement_outcome=settlement_outcome,
        settlement_value_per_contract=(
            Decimal("0") if settlement_outcome == "no" else Decimal("1")
        ),
        position_pnl=Decimal("100.00"),
        position_pnl_net_fees=Decimal("84.25"),
        position_return=Decimal("0.0843"),
        holding_period_days=29,
        annualized_return=Decimal("1.06"),
        tbill_rate_at_fill=Decimal("0.04"),
    )


def _cancelled(
    ticker: str = "T-CANCEL",
    *,
    bucket: str = "macro",
    structure: str = "single-binary",
    outcome: str = OUTCOME_STALE,
) -> PostEvent:
    return PostEvent(
        ticker=ticker,
        event_ticker=f"{ticker}-EVT",
        series_ticker="SERIES",
        primary_bucket=bucket,
        structure=structure,
        post_date=date(2026, 3, 1),
        side=SIDE_SELL_YES,
        limit_price=Decimal("0.10"),
        contracts_attempted=10000,
        capital_deployed=Decimal("1000.00"),
        outcome=outcome,
        fill_date=None,
        fill_price=None,
        total_fees=None,
        settlement_date=None,
        settlement_outcome=None,
        settlement_value_per_contract=None,
        position_pnl=None,
        position_pnl_net_fees=None,
        position_return=None,
        holding_period_days=None,
        annualized_return=None,
        tbill_rate_at_fill=None,
    )


def _blocked_by_cap(
    ticker: str = "T-BLOCKED",
    *,
    bucket: str = "macro",
    structure: str = "single-binary",
) -> PostEvent:
    return PostEvent(
        ticker=ticker,
        event_ticker=f"{ticker}-EVT",
        series_ticker="SERIES",
        primary_bucket=bucket,
        structure=structure,
        post_date=date(2026, 3, 1),
        side=SIDE_SELL_YES,
        limit_price=Decimal("0.10"),
        contracts_attempted=10000,
        capital_deployed=Decimal("1000.00"),
        outcome=OUTCOME_BLOCKED_BY_CAP,
        fill_date=None,
        fill_price=None,
        total_fees=None,
        settlement_date=None,
        settlement_outcome=None,
        settlement_value_per_contract=None,
        position_pnl=None,
        position_pnl_net_fees=None,
        position_return=None,
        holding_period_days=None,
        annualized_return=None,
        tbill_rate_at_fill=None,
    )


def _voided(
    ticker: str = "T-VOIDED",
    *,
    bucket: str = "geopolitics",
) -> PostEvent:
    """A voided filled position: outcome=filled, settlement_outcome=
    voided, total_fees=None, settlement_value_per_contract=None."""
    return PostEvent(
        ticker=ticker,
        event_ticker=f"{ticker}-EVT",
        series_ticker="SERIES",
        primary_bucket=bucket,
        structure="single-binary",
        post_date=date(2026, 3, 1),
        side=SIDE_SELL_YES,
        limit_price=Decimal("0.10"),
        contracts_attempted=10000,
        capital_deployed=Decimal("1000.00"),
        outcome=OUTCOME_FILLED,
        fill_date=date(2026, 3, 1),
        fill_price=Decimal("0.10"),
        total_fees=None,  # voided rule abstracts over fees
        settlement_date=date(2026, 4, 30),
        settlement_outcome="voided",
        settlement_value_per_contract=None,
        position_pnl=Decimal("9.86"),
        position_pnl_net_fees=Decimal("9.86"),
        position_return=Decimal("0.00986"),
        holding_period_days=60,
        annualized_return=Decimal("0.04"),
        tbill_rate_at_fill=Decimal("0.04"),
    )


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    import csv as _csv
    with path.open(newline="", encoding="utf-8") as f:
        return list(_csv.DictReader(f))


# ---------------------------------------------------------------------------
# Column order
# ---------------------------------------------------------------------------


def test_column_order_matches_locked_spec():
    """COLUMN_ORDER constant has 23 columns in the documented order."""
    assert len(COLUMN_ORDER) == 23
    # Spot-check a few key positions that locked decisions reference
    assert COLUMN_ORDER[0] == "ticker"
    assert COLUMN_ORDER[10] == "outcome"
    assert COLUMN_ORDER[13] == "total_fees"
    assert COLUMN_ORDER[-1] == "tbill_rate_at_fill"


def test_csv_header_matches_column_order():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.csv"
        write_positions_csv([_filled()], path)
        rows = _read_csv_rows(path)
        # csv.DictReader's fieldnames is the header
        import csv as _csv
        with path.open(encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            assert tuple(reader.fieldnames or ()) == COLUMN_ORDER


# ---------------------------------------------------------------------------
# Per-outcome cell-emptiness contract
# ---------------------------------------------------------------------------


def _columns_should_be_empty_for_cancelled() -> tuple[str, ...]:
    return (
        "fill_date", "fill_price", "total_fees",
        "settlement_date", "settlement_outcome",
        "settlement_value_per_contract",
        "position_pnl", "position_pnl_net_fees", "position_return",
        "holding_period_days", "annualized_return", "tbill_rate_at_fill",
    )


def test_stale_cancelled_row_has_empty_fill_and_settlement_cells():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.csv"
        write_positions_csv([_cancelled(outcome=OUTCOME_STALE)], path)
        rows = _read_csv_rows(path)
        assert len(rows) == 1
        r = rows[0]
        assert r["outcome"] == OUTCOME_STALE
        for col in _columns_should_be_empty_for_cancelled():
            assert r[col] == "", f"{col} should be empty for stale; got {r[col]!r}"


def test_out_of_zone_cancelled_row_has_empty_cells():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.csv"
        write_positions_csv(
            [_cancelled(outcome=OUTCOME_OUT_OF_ZONE)], path)
        rows = _read_csv_rows(path)
        r = rows[0]
        assert r["outcome"] == OUTCOME_OUT_OF_ZONE
        for col in _columns_should_be_empty_for_cancelled():
            assert r[col] == "", f"{col} should be empty for out_of_zone"


def test_blocked_by_cap_row_has_empty_fill_and_settlement_cells():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.csv"
        write_positions_csv([_blocked_by_cap()], path)
        rows = _read_csv_rows(path)
        r = rows[0]
        assert r["outcome"] == OUTCOME_BLOCKED_BY_CAP
        # Same emptiness contract as cancelled
        for col in _columns_should_be_empty_for_cancelled():
            assert r[col] == "", f"{col} should be empty for blocked_by_cap"
        # But capital_deployed and contracts_attempted ARE populated
        # (these describe the attempted post)
        assert r["capital_deployed"] != ""
        assert r["contracts_attempted"] != ""


def test_voided_row_has_empty_settlement_value_and_total_fees():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.csv"
        write_positions_csv([_voided()], path)
        rows = _read_csv_rows(path)
        r = rows[0]
        assert r["outcome"] == OUTCOME_FILLED
        assert r["settlement_outcome"] == "voided"
        assert r["settlement_value_per_contract"] == ""
        assert r["total_fees"] == ""
        # P&L fields are populated per the lockup rule
        assert r["position_pnl"] != ""
        assert r["position_pnl_net_fees"] != ""
        assert r["position_return"] != ""
        assert r["holding_period_days"] != ""
        assert r["annualized_return"] != ""
        assert r["tbill_rate_at_fill"] != ""
        # settlement_date populated (the effective/capped settlement)
        assert r["settlement_date"] != ""


def test_normal_filled_row_populated():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.csv"
        write_positions_csv([_filled()], path)
        r = _read_csv_rows(path)[0]
        assert r["outcome"] == OUTCOME_FILLED
        assert r["fill_date"] != ""
        assert r["fill_price"] != ""
        assert r["total_fees"] != ""
        assert r["settlement_date"] != ""
        assert r["settlement_outcome"] == "no"
        assert r["settlement_value_per_contract"] != ""
        assert r["position_pnl"] != ""


# ---------------------------------------------------------------------------
# Sort determinism + byte-identical re-run
# ---------------------------------------------------------------------------


def test_rows_sorted_by_ticker_then_post_date():
    """Rows are written in (ticker, post_date) ascending order."""
    events = [
        _filled(ticker="ZTOP", fill_date=date(2026, 3, 1)),
        _filled(ticker="A1", fill_date=date(2026, 3, 5)),
        _filled(ticker="A1", fill_date=date(2026, 3, 1)),
        _filled(ticker="MID", fill_date=date(2026, 3, 1)),
    ]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.csv"
        write_positions_csv(events, path)
        rows = _read_csv_rows(path)
    seq = [(r["ticker"], r["post_date"]) for r in rows]
    assert seq == sorted(seq)


def test_byte_identical_rerun():
    """Writing the same input twice produces byte-identical files."""
    events = [
        _filled(ticker="A", fill_date=date(2026, 3, 1)),
        _filled(ticker="B", fill_date=date(2026, 3, 2)),
        _cancelled(ticker="C", outcome=OUTCOME_STALE),
        _blocked_by_cap(ticker="D"),
        _voided(ticker="E"),
    ]
    with tempfile.TemporaryDirectory() as td:
        p1 = Path(td) / "out1.csv"
        p2 = Path(td) / "out2.csv"
        write_positions_csv(events, p1)
        # Pass a shuffled copy to confirm sort determinism
        write_positions_csv(list(reversed(events)), p2)
        assert p1.read_bytes() == p2.read_bytes()


def test_lf_line_endings_not_crlf():
    """Output uses LF line endings even on platforms that default to
    CRLF."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.csv"
        write_positions_csv([_filled()], path)
        raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert b"\n" in raw


def test_limit_price_formatted_to_4_decimal_places():
    """limit_price is rendered as 4 dp per simulator-design.md §4."""
    e = _filled(L="0.1")  # construct as Decimal('0.1')
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.csv"
        write_positions_csv([e], path)
        r = _read_csv_rows(path)[0]
    assert r["limit_price"] == "0.1000"
    assert r["fill_price"] == "0.1000"


# ---------------------------------------------------------------------------
# Diagnostics math
# ---------------------------------------------------------------------------


def test_funnel_counts_sum_to_total_attempted():
    events = [
        _filled(ticker=f"F{i}") for i in range(3)
    ] + [
        _cancelled(ticker=f"S{i}", outcome=OUTCOME_STALE) for i in range(2)
    ] + [
        _cancelled(ticker=f"O{i}", outcome=OUTCOME_OUT_OF_ZONE) for i in range(1)
    ] + [
        _blocked_by_cap(ticker="B1"),
    ]
    diag = compute_diagnostics(events)
    funnel = diag["funnel"]
    assert funnel["total_attempted"] == 7
    assert funnel[OUTCOME_FILLED] == 3
    assert funnel[OUTCOME_STALE] == 2
    assert funnel[OUTCOME_OUT_OF_ZONE] == 1
    assert funnel[OUTCOME_BLOCKED_BY_CAP] == 1
    # Components sum to total
    component_sum = sum(funnel[k] for k in (
        OUTCOME_FILLED, OUTCOME_STALE, OUTCOME_OUT_OF_ZONE,
        OUTCOME_BLOCKED_BY_CAP, "blocked_by_filter",
    )) + funnel["other"]
    assert component_sum == funnel["total_attempted"]


def test_per_bucket_has_5_rows():
    """Per-bucket diagnostic has exactly 5 rows for the 5 thesis
    buckets, even when some buckets have zero events."""
    events = [_filled(bucket="macro"), _filled(bucket="geopolitics")]
    diag = compute_diagnostics(events)
    assert set(diag["per_bucket"].keys()) == set(BUCKETS)
    assert len(BUCKETS) == 5


def test_per_structure_has_2_rows():
    """Per-structure diagnostic has exactly 2 rows (single-binary,
    multi-outcome-2-4)."""
    events = [_filled(structure="single-binary")]
    diag = compute_diagnostics(events)
    assert set(diag["per_structure"].keys()) == set(STRUCTURES)
    assert len(STRUCTURES) == 2


def test_fill_rate_calculation():
    """fill_rate_pct = filled / attempted * 100 in each bucket row."""
    events = [
        _filled(ticker=f"F{i}", bucket="macro") for i in range(3)
    ] + [
        _cancelled(ticker=f"S{i}", bucket="macro", outcome=OUTCOME_STALE)
        for i in range(2)
    ]
    diag = compute_diagnostics(events)
    macro = diag["per_bucket"]["macro"]
    assert macro["attempted"] == 5
    assert macro["filled"] == 3
    assert abs(macro["fill_rate_pct"] - 60.0) < 1e-9
    assert abs(macro["fill_rate_fraction"] - 0.6) < 1e-9


def test_capital_utilization_peak_geq_mean():
    """Peak >= mean by definition."""
    events = [
        _filled(ticker="A", fill_date=date(2026, 3, 1),
                settlement_date=date(2026, 3, 30)),
        _filled(ticker="B", fill_date=date(2026, 3, 5),
                settlement_date=date(2026, 3, 25)),
        _filled(ticker="C", fill_date=date(2026, 3, 10),
                settlement_date=date(2026, 3, 20)),
    ]
    diag = compute_diagnostics(events)
    util = diag["capital_utilization"]
    assert util["peak_capital_deployed"] >= util["mean_capital_deployed"]
    assert util["peak_capital_deployed"] > Decimal("0")


def test_capital_utilization_no_filled_returns_zero():
    """All-cancelled input yields zero peak / zero mean."""
    events = [_cancelled(outcome=OUTCOME_STALE) for _ in range(3)]
    diag = compute_diagnostics(events)
    util = diag["capital_utilization"]
    assert util["peak_capital_deployed"] == Decimal("0")
    assert util["mean_capital_deployed"] == Decimal("0")
    assert util["days_at_or_above_cap"] == 0


def test_capital_utilization_days_at_cap():
    """Synthetic 30-position fixture should report 1+ days at the
    $30K cap (the day after all 30 fill, before any settle)."""
    fill_d = date(2026, 3, 1)
    settle_d = date(2026, 3, 30)
    events = [
        _filled(ticker=f"T{i:02d}", fill_date=fill_d, settlement_date=settle_d,
                capital="1000.00")
        for i in range(30)
    ]
    diag = compute_diagnostics(events)
    util = diag["capital_utilization"]
    assert util["peak_capital_deployed"] == Decimal("30000.00")
    assert util["days_at_or_above_cap"] >= 1


def test_voided_count_non_negative_integer():
    """voided_count is a non-negative integer."""
    events = [_filled(), _voided(), _voided(ticker="V2"), _cancelled()]
    diag = compute_diagnostics(events)
    assert isinstance(diag["voided_count"], int)
    assert diag["voided_count"] == 2


def test_voided_count_zero_when_no_voided():
    events = [_filled() for _ in range(5)]
    diag = compute_diagnostics(events)
    assert diag["voided_count"] == 0


# ---------------------------------------------------------------------------
# write_diagnostics integration
# ---------------------------------------------------------------------------


def test_write_diagnostics_creates_text_file():
    events = [_filled() for _ in range(2)] + [_voided()]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "diagnostics.txt"
        diag = write_diagnostics(events, path)
        assert path.exists()
        text = path.read_text()
        assert "Test B simulator diagnostics" in text
        assert "Funnel" in text
        assert "Per-bucket fill rate" in text
        assert "Per-structure fill rate" in text
        assert "Capital utilization" in text
        assert "Voided positions" in text
    # Returned dict matches what compute_diagnostics produces
    assert diag["voided_count"] == 1


def test_write_diagnostics_contains_all_5_buckets_in_text():
    events = [_filled(bucket="macro")]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "diagnostics.txt"
        write_diagnostics(events, path)
        text = path.read_text()
    for bucket in BUCKETS:
        assert bucket in text


def test_write_diagnostics_contains_both_structures_in_text():
    events = [_filled(structure="single-binary")]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "diagnostics.txt"
        write_diagnostics(events, path)
        text = path.read_text()
    for structure in STRUCTURES:
        assert structure in text
