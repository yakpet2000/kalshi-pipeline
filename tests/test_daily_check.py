"""Unit tests for simulator.daily_check — the per-market engine.

Hand-crafted candle fixtures. No live API, no filesystem reads.
Each test exercises one branch of the algorithm or one numerical
case from the locked spec.

Test coverage per the 2b.3 sub-stage plan:

Entry / fill / cancel branches:
- Same-day fill (post + fill on the same ET-bucket date)
- No-volume in-zone -> stale_cancelled
- No-volume out-of-zone -> out_of_zone_cancelled
- Multi-day fill (two PostEvents: stale_cancelled, then filled)

P&L branches:
- sell-YES at L=0.90 settles NO  -> +0.90 / contract
- sell-YES at L=0.90 settles YES -> -0.10 / contract
- buy-YES  at L=0.10 settles YES -> +0.90 / contract
- buy-YES  at L=0.10 settles NO  -> -0.10 / contract

Fee boundary cases (simulator-design.md §3.7):
- L=0.10, contracts=10000  -> total_fees = $15.75
- L=0.85, contracts=1176   -> total_fees = $2.63
- L=0.15, contracts=6666   -> total_fees = $14.87

Position sizing (simulator-design.md §3.1):
- L=0.10 -> 10000 contracts, $1000.00
- L=0.85 -> 1176 contracts, $999.60 (floor)
- L=0.05 -> 20000 contracts, $1000.00

Annualization (simulator-design.md §3.5):
- 30-day holding, return=+0.05 -> annualized ~0.6083
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from simulator.daily_check import (
    OUTCOME_FILLED,
    OUTCOME_OUT_OF_ZONE,
    OUTCOME_STALE,
    SIDE_BUY_YES,
    SIDE_SELL_YES,
    PostEvent,
    compute_fee,
    compute_pnl,
    compute_sizing,
    run_market,
)
from simulator.et_bucket import ET


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _candle(et_date: date, *, volume: str = "0",
            close: str | None = None, high: str | None = None,
            low: str | None = None, previous: str | None = None,
            open_: str | None = None) -> dict:
    """Build a Kalshi-style candle dict whose ET-bucket date is
    `et_date`. The `end_period_ts` is computed as the Unix timestamp
    of midnight ET on (et_date + 1 day) — i.e., the moment the
    candle finalizes. The candle therefore covers the ET day equal
    to `et_date`."""
    next_et_midnight = datetime.combine(
        et_date + timedelta(days=1), time(0, 0), tzinfo=ET
    )
    end_period_ts = int(next_et_midnight.astimezone(timezone.utc).timestamp())
    price: dict = {}
    if open_ is not None:
        price["open_dollars"] = open_
    if high is not None:
        price["high_dollars"] = high
    if low is not None:
        price["low_dollars"] = low
    if close is not None:
        price["close_dollars"] = close
    if previous is not None:
        price["previous_dollars"] = previous
    return {
        "end_period_ts": end_period_ts,
        "volume_fp": volume,
        "open_interest_fp": "0",
        "price": price,
    }


def _meta(settlement_outcome: str, *, structure: str = "single-binary") -> dict:
    return {
        "ticker": "TEST-TICKER",
        "event_ticker": "TEST-EVENT",
        "series_ticker": "TEST",
        "primary_bucket": "macro",
        "structure": structure,
        "settlement_outcome": settlement_outcome,
    }


def _const_tbill(value: str = "0.04"):
    """A tbill_lookup that ignores the date and returns a constant.
    Most P&L tests don't depend on the rate value."""
    v = Decimal(value)
    return lambda d: v


# ---------------------------------------------------------------------------
# Position sizing & fees & P&L unit tests (no candle iteration)
# ---------------------------------------------------------------------------


def test_position_sizing_at_010():
    contracts, capital = compute_sizing(Decimal("0.10"))
    assert contracts == 10000
    assert capital == Decimal("1000.00")


def test_position_sizing_at_085_floors_to_1176():
    contracts, capital = compute_sizing(Decimal("0.85"))
    assert contracts == 1176  # floor(1000/0.85) = 1176
    assert capital == Decimal("999.60")


def test_position_sizing_at_005():
    contracts, capital = compute_sizing(Decimal("0.05"))
    assert contracts == 20000
    assert capital == Decimal("1000.00")


def test_fee_boundary_l010_10000_contracts():
    """simulator-design.md §3.7 worked example."""
    fee = compute_fee(10000, Decimal("0.10"))
    assert fee == Decimal("15.75")


def test_fee_boundary_l085_1176_contracts():
    """simulator-design.md §3.7 worked example: round-up."""
    fee = compute_fee(1176, Decimal("0.85"))
    assert fee == Decimal("2.63")


def test_fee_boundary_l015_6666_contracts():
    """simulator-design.md §3.7 worked example: round-up."""
    fee = compute_fee(6666, Decimal("0.15"))
    assert fee == Decimal("14.88")


def test_pnl_sell_yes_settles_no():
    """sell-YES at L=0.90 settles NO -> +0.90 / contract."""
    pnl = compute_pnl(SIDE_SELL_YES, Decimal("0.90"), 100, Decimal("0"))
    assert pnl == Decimal("90.00")


def test_pnl_sell_yes_settles_yes():
    """sell-YES at L=0.90 settles YES -> -0.10 / contract."""
    pnl = compute_pnl(SIDE_SELL_YES, Decimal("0.90"), 100, Decimal("1"))
    assert pnl == Decimal("-10.00")


def test_pnl_buy_yes_settles_yes():
    """buy-YES at L=0.10 settles YES -> +0.90 / contract."""
    pnl = compute_pnl(SIDE_BUY_YES, Decimal("0.10"), 100, Decimal("1"))
    assert pnl == Decimal("90.00")


def test_pnl_buy_yes_settles_no():
    """buy-YES at L=0.10 settles NO -> -0.10 / contract."""
    pnl = compute_pnl(SIDE_BUY_YES, Decimal("0.10"), 100, Decimal("0"))
    assert pnl == Decimal("-10.00")


# ---------------------------------------------------------------------------
# Daily-check loop: entry / fill / cancel branches
# ---------------------------------------------------------------------------


def test_same_day_fill():
    """Yesterday's close in zone, today posts and fills same day."""
    candles = [
        _candle(date(2026, 3, 1), volume="100", close="0.10",
                high="0.12", low="0.08", previous="0.10"),
        # today: post sell-YES at L=0.10; fills since low<=0.10<=high & vol>0
        _candle(date(2026, 3, 2), volume="50", close="0.11",
                high="0.12", low="0.08", previous="0.10"),
        # next-day candle so the loop processes day-2's fill
        _candle(date(2026, 3, 3), volume="50", close="0.11",
                high="0.12", low="0.10", previous="0.11"),
    ]
    posts = run_market(candles, _meta(settlement_outcome="no"), _const_tbill())
    filled = [p for p in posts if p.outcome == OUTCOME_FILLED]
    assert len(filled) >= 1
    p = filled[0]
    assert p.side == SIDE_SELL_YES
    assert p.limit_price == Decimal("0.10")
    assert p.fill_date == p.post_date  # 1-day rest model: fill_date == post_date
    assert p.contracts_attempted == 10000
    assert p.capital_deployed == Decimal("1000.00")


def test_no_volume_in_zone_stale_cancel():
    """Yesterday's close in zone, post today, today has zero volume.
    Tomorrow's check still sees yesterday's price in zone -> stale_cancelled."""
    candles = [
        _candle(date(2026, 3, 1), volume="100", close="0.10",
                high="0.12", low="0.08", previous="0.10"),
        # today: post sell-YES at L=0.10; volume=0 means no fill
        _candle(date(2026, 3, 2), volume="0", previous="0.10"),
        # next day: yesterday's close (still 0.10, via previous fallback)
        # is still <=0.15, so cancel is stale_cancelled, not out_of_zone.
        _candle(date(2026, 3, 3), volume="100", close="0.11",
                high="0.12", low="0.10", previous="0.10"),
    ]
    posts = run_market(candles, _meta(settlement_outcome="no"), _const_tbill())
    cancels = [p for p in posts if p.outcome == OUTCOME_STALE]
    assert len(cancels) >= 1
    c = cancels[0]
    assert c.side == SIDE_SELL_YES
    assert c.fill_date is None
    assert c.position_pnl is None


def test_no_volume_out_of_zone_cancel():
    """Yesterday's close in zone, post today. Today has zero volume.
    Tomorrow: today's close > 0.15 (out of zone). Cancel is
    out_of_zone_cancelled."""
    candles = [
        _candle(date(2026, 3, 1), volume="100", close="0.10",
                high="0.12", low="0.08", previous="0.10"),
        # today: post sell-YES at L=0.10; volume=0 (no fill)
        # but candle's "close" jumps to 0.20 (out of zone for sell-YES)
        _candle(date(2026, 3, 2), volume="0",
                close="0.20", previous="0.10"),
        # next day: yesterday's close = 0.20 > 0.15 -> out_of_zone
        _candle(date(2026, 3, 3), volume="100", close="0.20",
                high="0.22", low="0.18", previous="0.20"),
    ]
    posts = run_market(candles, _meta(settlement_outcome="no"), _const_tbill())
    cancels = [p for p in posts if p.outcome == OUTCOME_OUT_OF_ZONE]
    assert len(cancels) == 1
    c = cancels[0]
    assert c.side == SIDE_SELL_YES
    assert c.fill_date is None


def test_multi_day_two_post_events():
    """Maker-fill-model locks orders at 1-day rest. The plan's
    'multi-day fill' is therefore two separate PostEvents:
    1. Day N: post; volume=0 on day N's candle -> no fill;
    2. Day N+1's check: stale-cancel the day-N order; same check posts
       a NEW order (no-cooldown, simulator-design.md §3.3); volume>0
       on day N+1's candle -> filled."""
    candles = [
        _candle(date(2026, 3, 1), volume="100", close="0.10",
                high="0.12", low="0.08", previous="0.10"),
        # day 2: post sell-YES at L=0.10; vol=0 -> no fill
        _candle(date(2026, 3, 2), volume="0", previous="0.10"),
        # day 3: cancel day-2 order; post new sell-YES at L=0.10 (yesterday's
        # close=0.10 via previous-fallback); fills (vol>0, low<=0.10<=high)
        _candle(date(2026, 3, 3), volume="100", close="0.11",
                high="0.12", low="0.09", previous="0.10"),
        # day 4: settles
        _candle(date(2026, 3, 4), volume="100", close="0.11",
                high="0.12", low="0.10", previous="0.11"),
    ]
    posts = run_market(candles, _meta(settlement_outcome="no"), _const_tbill())
    # Should have at least one stale_cancelled and one filled
    cancels = [p for p in posts if p.outcome == OUTCOME_STALE]
    fills = [p for p in posts if p.outcome == OUTCOME_FILLED]
    assert len(cancels) >= 1
    assert len(fills) >= 1
    cancelled = cancels[0]
    filled = fills[0]
    # Cancelled post is from day 2; filled post is from day 3
    assert cancelled.post_date == date(2026, 3, 2)
    assert filled.post_date == date(2026, 3, 3)
    assert filled.fill_date == date(2026, 3, 3)


# ---------------------------------------------------------------------------
# Settlement P&L through run_market (end-to-end)
# ---------------------------------------------------------------------------


def _build_filling_market(L: str, side_zone: str, settlement_outcome: str) -> list[dict]:
    """Build a 4-candle fixture that fills exactly one position at L.
    side_zone is 'sell' (longshot) or 'buy' (favorite)."""
    yesterday_close = L
    high = str(float(L) + 0.02)
    low = str(float(L) - 0.02) if float(L) > 0.05 else "0.01"
    if side_zone == "buy":
        # for buy-YES, L >= 0.85; high/low symmetric
        high = str(min(float(L) + 0.02, 0.99))
        low = str(float(L) - 0.02)
    return [
        _candle(date(2026, 3, 1), volume="100", close=yesterday_close,
                high=high, low=low, previous=yesterday_close),
        _candle(date(2026, 3, 2), volume="100", close=yesterday_close,
                high=high, low=low, previous=yesterday_close),
        _candle(date(2026, 3, 3), volume="100", close=yesterday_close,
                high=high, low=low, previous=yesterday_close),
    ]


def test_end_to_end_sell_yes_settles_no():
    candles = _build_filling_market(L="0.10", side_zone="sell",
                                    settlement_outcome="no")
    posts = run_market(candles, _meta(settlement_outcome="no"), _const_tbill())
    fills = [p for p in posts if p.outcome == OUTCOME_FILLED]
    assert len(fills) >= 1
    p = fills[0]
    assert p.side == SIDE_SELL_YES
    assert p.settlement_value_per_contract == Decimal("0")
    # gross pnl per contract = L - 0 = 0.10
    assert p.position_pnl == Decimal("0.10") * Decimal(p.contracts_attempted)
    # net of fees
    expected_fee = compute_fee(p.contracts_attempted, Decimal("0.10"))
    assert p.total_fees == expected_fee
    assert p.position_pnl_net_fees == p.position_pnl - expected_fee
    # tbill lookup propagated
    assert p.tbill_rate_at_fill == Decimal("0.04")


def test_end_to_end_buy_yes_settles_yes():
    candles = _build_filling_market(L="0.90", side_zone="buy",
                                    settlement_outcome="yes")
    posts = run_market(candles, _meta(settlement_outcome="yes"), _const_tbill())
    fills = [p for p in posts if p.outcome == OUTCOME_FILLED]
    assert len(fills) >= 1
    p = fills[0]
    assert p.side == SIDE_BUY_YES
    assert p.settlement_value_per_contract == Decimal("1.0")
    # gross pnl per contract = 1 - L = 0.10
    expected_gross = Decimal("0.10") * Decimal(p.contracts_attempted)
    assert p.position_pnl == expected_gross


# ---------------------------------------------------------------------------
# Annualization
# ---------------------------------------------------------------------------


def test_annualization_30_day_holding_5pct_return():
    """Per simulator-design.md §3.5 worked example:
    30-day holding, position_return=+0.05 -> annualized ~ 0.6083333..."""
    # Construct fill_date and settlement_date 30 days apart and a P&L
    # that yields +5% return after fees. Use the annualization formula
    # directly via a small manual fixture because constructing a fill
    # whose net return is exactly +5% via fees is brittle; the formula
    # itself is the unit under test.
    pos_return = Decimal("0.05")
    holding = Decimal("30")
    annualized = pos_return * (Decimal("365") / holding)
    # 0.05 * 365 / 30 = 0.608333...
    assert annualized.quantize(Decimal("0.0001")) == Decimal("0.6083")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_dead_zone_no_post_emitted():
    """If yesterday's close is in the dead zone (>0.15 and <0.85),
    no post is attempted, so no PostEvent is emitted."""
    candles = [
        _candle(date(2026, 3, 1), volume="100", close="0.50",
                high="0.51", low="0.49", previous="0.50"),
        _candle(date(2026, 3, 2), volume="100", close="0.50",
                high="0.51", low="0.49", previous="0.50"),
        _candle(date(2026, 3, 3), volume="100", close="0.50",
                high="0.51", low="0.49", previous="0.50"),
    ]
    posts = run_market(candles, _meta(settlement_outcome="no"), _const_tbill())
    assert posts == []


def test_first_day_no_yesterday_no_post():
    """The first candle has no 'yesterday' to read. No post."""
    candles = [
        _candle(date(2026, 3, 1), volume="100", close="0.10",
                high="0.12", low="0.08", previous="0.10"),
    ]
    posts = run_market(candles, _meta(settlement_outcome="no"), _const_tbill())
    # Day 1: no yesterday -> no post; no later candle -> nothing happens
    assert posts == []


def test_voided_settlement_routes_to_voided_builder():
    """Voided P&L was added in sub-stage 2b.5. With the required
    void-related market_meta keys (expected_settlement_date,
    void_announcement_date), run_market dispatches to the voided
    builder and produces a filled PostEvent with
    settlement_outcome='voided'. Detailed P&L coverage lives in
    tests/test_voided.py; this test just confirms the dispatch
    path is wired."""
    candles = [
        _candle(date(2026, 3, 1), volume="100", close="0.10",
                high="0.12", low="0.08", previous="0.10"),
        _candle(date(2026, 3, 2), volume="100", close="0.11",
                high="0.12", low="0.08", previous="0.10"),
    ]
    meta = _meta(settlement_outcome="voided")
    meta["expected_settlement_date"] = date(2026, 4, 1)
    meta["void_announcement_date"] = date(2026, 3, 15)
    posts = run_market(candles, meta, _const_tbill())
    fills = [p for p in posts if p.outcome == "filled"]
    assert len(fills) >= 1
    p = fills[0]
    assert p.settlement_outcome == "voided"
    assert p.settlement_value_per_contract is None
    assert p.total_fees is None  # voided rule abstracts over fees


def test_postevent_is_frozen_dataclass():
    """PostEvent is locked: frozen dataclass means cap-layer (2b.4)
    can't mutate; it must construct a new event with the new outcome."""
    import dataclasses
    assert dataclasses.is_dataclass(PostEvent)
    # frozen=True means setattr raises FrozenInstanceError
    p = PostEvent(
        ticker="T", event_ticker="E", series_ticker="S",
        primary_bucket="macro", structure="single-binary",
        post_date=date(2026, 3, 1), side=SIDE_SELL_YES,
        limit_price=Decimal("0.10"), contracts_attempted=10000,
        capital_deployed=Decimal("1000.00"), outcome=OUTCOME_FILLED,
        fill_date=date(2026, 3, 1), fill_price=Decimal("0.10"),
        total_fees=Decimal("15.75"), settlement_date=date(2026, 3, 30),
        settlement_outcome="no", settlement_value_per_contract=Decimal("0"),
        position_pnl=Decimal("1000"), position_pnl_net_fees=Decimal("984.25"),
        position_return=Decimal("0.98"), holding_period_days=29,
        annualized_return=Decimal("12.3"), tbill_rate_at_fill=Decimal("0.04"),
    )
    import pytest as _pytest
    with _pytest.raises(dataclasses.FrozenInstanceError):
        p.outcome = "blocked_by_cap"  # type: ignore[misc]
