"""Unit tests for voided-market handling in simulator/daily_check.py.

Sub-stage 2b.5. Implementation choice: Option (a) — voided handling
is co-located inside daily_check.py rather than in a separate
wrapper layer. The voided builder (`_build_voided_filled_event`)
applies the T-bill-over-lockup rule per
notes/voided-market-detection.md §4.

Coverage per the 2b.5 sub-stage plan:
- Lockup spans 90 days, T-bill 0.04 → P&L per contract matches the
  formula in voided-market-detection.md §4
- Holding-period cap: original settlement day 60, void announcement
  day 90 → holding_period_days = 60
- Holding-period cap: original settlement day 90, void announcement
  day 60 → holding_period_days = 60 (the EARLIEST of the two)
- Schema: voided rows have empty settlement_value_per_contract

All inputs are synthetic candle fixtures; no live data, no I/O.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from simulator.daily_check import (
    OUTCOME_FILLED,
    SETTLE_VOIDED,
    SIDE_SELL_YES,
    _build_voided_filled_event,
    run_market,
)
from simulator.et_bucket import ET


# ---------------------------------------------------------------------------
# Fixture helpers (mirror tests/test_daily_check.py)
# ---------------------------------------------------------------------------


def _candle(et_date: date, *, volume: str = "0",
            close: str | None = None, high: str | None = None,
            low: str | None = None, previous: str | None = None) -> dict:
    next_et_midnight = datetime.combine(
        et_date + timedelta(days=1), time(0, 0), tzinfo=ET
    )
    end_period_ts = int(next_et_midnight.astimezone(timezone.utc).timestamp())
    price: dict = {}
    if close is not None:
        price["close_dollars"] = close
    if high is not None:
        price["high_dollars"] = high
    if low is not None:
        price["low_dollars"] = low
    if previous is not None:
        price["previous_dollars"] = previous
    return {
        "end_period_ts": end_period_ts,
        "volume_fp": volume,
        "open_interest_fp": "0",
        "price": price,
    }


def _post_meta(L: str = "0.10", contracts: int = 10000,
               capital: str = "1000.00") -> dict:
    return {
        "ticker": "TEST-V",
        "event_ticker": "EVT",
        "series_ticker": "SERIES",
        "primary_bucket": "geopolitics",
        "structure": "single-binary",
        "post_date": date(2026, 3, 1),
        "side": SIDE_SELL_YES,
        "limit_price": Decimal(L),
        "contracts_attempted": contracts,
        "capital_deployed": Decimal(capital),
    }


def _const_tbill(value: str = "0.04"):
    v = Decimal(value)
    return lambda d: v


# ---------------------------------------------------------------------------
# Plan branch 1: lockup spans 90 days, T-bill 0.04 → formula match
# ---------------------------------------------------------------------------


def test_lockup_90_days_tbill_004():
    """Per voided-market-detection.md §4: position contributes
    return = tbill * lockup_days / 365 over the lockup period.
    For 90-day lockup at 0.04 tbill:
      position_return = 0.04 * 90 / 365 = 0.00986301369...
      position_pnl_net_fees = capital * position_return
                            = 1000 * 0.00986... = 9.8630...
    """
    fill_date = date(2026, 3, 1)
    expected_settlement = date(2026, 5, 30)  # 90 days after fill
    void_announcement = date(2026, 6, 15)    # later than expected; cap by expected
    p = _build_voided_filled_event(
        post_meta=_post_meta(L="0.10", contracts=10000, capital="1000.00"),
        fill_date=fill_date,
        void_announcement_date=void_announcement,
        expected_settlement_date=expected_settlement,
        tbill_lookup=_const_tbill("0.04"),
    )
    assert p.holding_period_days == 90
    expected_return = Decimal("0.04") * Decimal(90) / Decimal(365)
    assert p.position_return == expected_return
    expected_pnl = Decimal("1000.00") * expected_return
    assert p.position_pnl_net_fees == expected_pnl
    assert p.position_pnl == expected_pnl  # rule treats as net
    assert p.tbill_rate_at_fill == Decimal("0.04")


# ---------------------------------------------------------------------------
# Plan branch 2: cap to expected settlement when void is later
# ---------------------------------------------------------------------------


def test_holding_period_cap_void_later_than_expected():
    """Original expected settlement at fill+60 days, void
    announcement at fill+90 days. lockup is the earlier of the
    two = expected_settlement → holding_period_days = 60."""
    fill_date = date(2026, 3, 1)
    expected_settlement = date(2026, 4, 30)  # fill + 60 days
    void_announcement = date(2026, 5, 30)    # fill + 90 days (later)
    p = _build_voided_filled_event(
        post_meta=_post_meta(),
        fill_date=fill_date,
        void_announcement_date=void_announcement,
        expected_settlement_date=expected_settlement,
        tbill_lookup=_const_tbill("0.04"),
    )
    assert p.holding_period_days == 60
    assert p.settlement_date == expected_settlement


# ---------------------------------------------------------------------------
# Plan branch 3: cap to void announcement when void is earlier
# ---------------------------------------------------------------------------


def test_holding_period_cap_void_earlier_than_expected():
    """Original expected settlement at fill+90 days, void
    announcement at fill+60 days. lockup is the earlier of the
    two = void_announcement → holding_period_days = 60."""
    fill_date = date(2026, 3, 1)
    expected_settlement = date(2026, 5, 30)  # fill + 90 days
    void_announcement = date(2026, 4, 30)    # fill + 60 days (earlier)
    p = _build_voided_filled_event(
        post_meta=_post_meta(),
        fill_date=fill_date,
        void_announcement_date=void_announcement,
        expected_settlement_date=expected_settlement,
        tbill_lookup=_const_tbill("0.04"),
    )
    assert p.holding_period_days == 60
    assert p.settlement_date == void_announcement


# ---------------------------------------------------------------------------
# Plan branch 4: schema verification — voided rows
# ---------------------------------------------------------------------------


def test_voided_schema_fields():
    """Voided positions have empty settlement_value_per_contract,
    populated position_pnl_net_fees per the lockup formula,
    settlement_outcome='voided', total_fees=None (rule abstracts
    over fees)."""
    p = _build_voided_filled_event(
        post_meta=_post_meta(),
        fill_date=date(2026, 3, 1),
        void_announcement_date=None,  # fall back to expected
        expected_settlement_date=date(2026, 5, 30),
        tbill_lookup=_const_tbill("0.04"),
    )
    assert p.outcome == OUTCOME_FILLED
    assert p.settlement_outcome == SETTLE_VOIDED
    assert p.settlement_value_per_contract is None
    assert p.total_fees is None
    assert p.position_pnl is not None
    assert p.position_pnl_net_fees is not None
    assert p.position_return is not None
    assert p.holding_period_days is not None
    assert p.annualized_return is not None
    assert p.tbill_rate_at_fill == Decimal("0.04")


# ---------------------------------------------------------------------------
# Additional voided-handling tests
# ---------------------------------------------------------------------------


def test_void_announcement_none_falls_back_to_expected():
    """When void_announcement_date is None, the rule falls back to
    expected_settlement_date alone for the lockup cap."""
    fill_date = date(2026, 3, 1)
    expected = date(2026, 5, 30)
    p = _build_voided_filled_event(
        post_meta=_post_meta(),
        fill_date=fill_date,
        void_announcement_date=None,
        expected_settlement_date=expected,
        tbill_lookup=_const_tbill("0.04"),
    )
    assert p.settlement_date == expected
    assert p.holding_period_days == 90


def test_annualized_return_collapses_to_tbill_rate():
    """The annualization formula
        annualized = position_return * 365 / holding_period_days
    collapses to tbill_rate exactly for voided positions, since
    position_return = tbill_rate * holding_period_days / 365.
    Verify the simulator records the collapsed value, not a
    rounding artifact."""
    p = _build_voided_filled_event(
        post_meta=_post_meta(),
        fill_date=date(2026, 3, 1),
        void_announcement_date=None,
        expected_settlement_date=date(2026, 5, 30),
        tbill_lookup=_const_tbill("0.04"),
    )
    # position_return = 0.04 * 90/365; annualized = position_return * 365/90 = 0.04
    assert p.annualized_return == Decimal("0.04")


def test_same_day_fill_and_void_clamps_holding_period_to_one():
    """Same convention as _build_filled_event: if effective settlement
    is on the same day as fill, holding_period_days clamps to 1 to
    keep the formula well-defined."""
    fill_date = date(2026, 3, 1)
    p = _build_voided_filled_event(
        post_meta=_post_meta(),
        fill_date=fill_date,
        void_announcement_date=fill_date,  # same day
        expected_settlement_date=date(2026, 5, 30),
        tbill_lookup=_const_tbill("0.04"),
    )
    assert p.holding_period_days == 1


def test_run_market_dispatches_to_voided_for_voided_outcome():
    """End-to-end: run_market with settlement_outcome='voided' and
    the required void-related market_meta keys produces a voided
    PostEvent."""
    candles = [
        _candle(date(2026, 3, 1), volume="100", close="0.10",
                high="0.12", low="0.08", previous="0.10"),
        _candle(date(2026, 3, 2), volume="100", close="0.10",
                high="0.12", low="0.08", previous="0.10"),
    ]
    market_meta = {
        "ticker": "VOIDED-TEST",
        "event_ticker": "VOIDED-EVT",
        "series_ticker": "VOIDED-SERIES",
        "primary_bucket": "geopolitics",
        "structure": "single-binary",
        "settlement_outcome": "voided",
        "expected_settlement_date": date(2026, 5, 30),
        "void_announcement_date": date(2026, 4, 15),
    }
    posts = run_market(candles, market_meta, _const_tbill("0.04"))
    fills = [p for p in posts if p.outcome == OUTCOME_FILLED]
    assert len(fills) >= 1
    p = fills[0]
    assert p.settlement_outcome == "voided"
    # void_announcement (4/15) is earlier than expected_settlement (5/30)
    # so settlement_date is the void announcement
    assert p.settlement_date == date(2026, 4, 15)


def test_voided_position_pnl_is_positive_for_positive_tbill():
    """Voided positions earn the T-bill rate × lockup. For a positive
    rate, position_pnl_net_fees should be strictly positive (the
    maker recovered capital plus opportunity-cost yield)."""
    p = _build_voided_filled_event(
        post_meta=_post_meta(capital="1000.00"),
        fill_date=date(2026, 3, 1),
        void_announcement_date=None,
        expected_settlement_date=date(2026, 6, 1),
        tbill_lookup=_const_tbill("0.05"),
    )
    assert p.position_pnl_net_fees > Decimal("0")


def test_voided_zero_tbill_gives_zero_pnl():
    """If the T-bill rate is zero on the fill date, the voided
    position contributes zero P&L (no opportunity cost recovered)."""
    p = _build_voided_filled_event(
        post_meta=_post_meta(),
        fill_date=date(2026, 3, 1),
        void_announcement_date=None,
        expected_settlement_date=date(2026, 6, 1),
        tbill_lookup=_const_tbill("0"),
    )
    assert p.position_pnl_net_fees == Decimal("0")
    assert p.annualized_return == Decimal("0")
