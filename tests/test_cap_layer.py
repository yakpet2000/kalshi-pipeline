"""Unit tests for simulator.cap_layer — capital cap + tiebreaker.

All inputs are synthetic PostEvent streams; no candle iteration, no
network, no filesystem reads. Each test exercises one branch of the
admission algorithm or one scenario from the 2b.4 sub-stage plan.

Coverage (per the plan):
- 30 same-day posts at $1000 each → all admitted; 31st blocked_by_cap
- Capital frees on settlement: post on day N fills, settles day M;
  post on day M+1 admitted (capital freed)
- Tiebreaker: identical open_time → alphabetical by ticker
- Cap counts only filled positions, not resting (cancelled) orders
- Multi-outcome same-event same-day: both posts attempted; cap and
  tiebreaker apply globally, not per-event
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from simulator.cap_layer import (
    CAPITAL_CAP,
    OUTCOME_BLOCKED_BY_CAP,
    apply_cap_layer,
)
from simulator.daily_check import (
    OUTCOME_FILLED,
    OUTCOME_OUT_OF_ZONE,
    OUTCOME_STALE,
    SIDE_SELL_YES,
    PostEvent,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _filled_event(
    ticker: str,
    post_date: date,
    *,
    capital: str = "1000.00",
    fill_date: date | None = None,
    settlement_date: date | None = None,
    event_ticker: str = "EVT",
    structure: str = "single-binary",
) -> PostEvent:
    """Build a 'filled' PostEvent with the minimum fields the cap
    layer reads. Most carried-from-meta and P&L fields are filled
    with arbitrary placeholder values — the cap layer doesn't read
    them."""
    if fill_date is None:
        fill_date = post_date
    if settlement_date is None:
        settlement_date = post_date  # arbitrary; tests override when relevant
    cap = Decimal(capital)
    return PostEvent(
        ticker=ticker,
        event_ticker=event_ticker,
        series_ticker="SERIES",
        primary_bucket="macro",
        structure=structure,
        post_date=post_date,
        side=SIDE_SELL_YES,
        limit_price=Decimal("0.10"),
        contracts_attempted=10000,
        capital_deployed=cap,
        outcome=OUTCOME_FILLED,
        fill_date=fill_date,
        fill_price=Decimal("0.10"),
        total_fees=Decimal("15.75"),
        settlement_date=settlement_date,
        settlement_outcome="no",
        settlement_value_per_contract=Decimal("0"),
        position_pnl=cap,
        position_pnl_net_fees=cap - Decimal("15.75"),
        position_return=Decimal("0.98"),
        holding_period_days=max((settlement_date - fill_date).days, 1),
        annualized_return=Decimal("0.98"),
        tbill_rate_at_fill=Decimal("0.04"),
    )


def _cancelled_event(
    ticker: str,
    post_date: date,
    outcome: str,
    *,
    capital: str = "1000.00",
    event_ticker: str = "EVT",
) -> PostEvent:
    """Build a cancelled PostEvent (no fill, no settlement, no P&L)."""
    return PostEvent(
        ticker=ticker,
        event_ticker=event_ticker,
        series_ticker="SERIES",
        primary_bucket="macro",
        structure="single-binary",
        post_date=post_date,
        side=SIDE_SELL_YES,
        limit_price=Decimal("0.10"),
        contracts_attempted=10000,
        capital_deployed=Decimal(capital),
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


def _open_time_map(*tickers: str) -> dict[str, str]:
    """Build a default open_time map: each ticker has a unique
    open_time so the tiebreaker doesn't bind. Caller can override
    individual entries when they want a tie."""
    return {t: f"2025-01-{i + 1:02d}T00:00:00Z" for i, t in enumerate(tickers)}


# ---------------------------------------------------------------------------
# Plan branch 1: 30 admitted, 31st blocked_by_cap
# ---------------------------------------------------------------------------


def test_30_posts_admit_31st_blocked_by_cap():
    """30 posts at $1000 each on the same day fit exactly under $30K
    cap. The 31st pushes total to $31K and is blocked_by_cap."""
    pd = date(2026, 3, 1)
    sd = date(2026, 4, 1)
    tickers = [f"T{i:02d}" for i in range(31)]
    events = [
        _filled_event(t, pd, capital="1000.00", fill_date=pd, settlement_date=sd)
        for t in tickers
    ]
    out = apply_cap_layer(events, _open_time_map(*tickers))

    n_filled = sum(1 for e in out if e.outcome == OUTCOME_FILLED)
    n_blocked = sum(1 for e in out if e.outcome == OUTCOME_BLOCKED_BY_CAP)
    assert n_filled == 30
    assert n_blocked == 1
    assert len(out) == 31


def test_blocked_by_cap_clears_fill_and_pnl_fields():
    """Relabeled events have all fill-time / settlement / P&L fields
    cleared per simulator-design.md §4 final paragraph."""
    pd = date(2026, 3, 1)
    sd = date(2026, 4, 1)
    tickers = [f"T{i:02d}" for i in range(31)]
    events = [
        _filled_event(t, pd, fill_date=pd, settlement_date=sd) for t in tickers
    ]
    out = apply_cap_layer(events, _open_time_map(*tickers))
    blocked = [e for e in out if e.outcome == OUTCOME_BLOCKED_BY_CAP]
    assert len(blocked) == 1
    b = blocked[0]
    # Carried-from-meta and post-time fields stay
    assert b.ticker.startswith("T")
    assert b.post_date == pd
    assert b.capital_deployed == Decimal("1000.00")
    # Fill/settlement/P&L fields cleared
    assert b.fill_date is None
    assert b.fill_price is None
    assert b.total_fees is None
    assert b.settlement_date is None
    assert b.settlement_outcome is None
    assert b.settlement_value_per_contract is None
    assert b.position_pnl is None
    assert b.position_pnl_net_fees is None
    assert b.position_return is None
    assert b.holding_period_days is None
    assert b.annualized_return is None
    assert b.tbill_rate_at_fill is None


def test_30_admitted_no_blocks_when_exactly_at_cap():
    """30 × $1000 = $30000 = cap exactly. None should be blocked
    (the spec uses strict-greater-than: > 30000 blocks)."""
    pd = date(2026, 3, 1)
    sd = date(2026, 4, 1)
    tickers = [f"T{i:02d}" for i in range(30)]
    events = [
        _filled_event(t, pd, capital="1000.00", fill_date=pd, settlement_date=sd)
        for t in tickers
    ]
    out = apply_cap_layer(events, _open_time_map(*tickers))
    assert sum(1 for e in out if e.outcome == OUTCOME_FILLED) == 30
    assert sum(1 for e in out if e.outcome == OUTCOME_BLOCKED_BY_CAP) == 0


# ---------------------------------------------------------------------------
# Plan branch 2: capital frees on settlement
# ---------------------------------------------------------------------------


def test_capital_frees_after_settlement():
    """30 posts on day N fill and settle on day M. A 31st post on
    day M+1 should be admitted (the day-N capital has been
    released)."""
    day_n = date(2026, 3, 1)
    day_m = date(2026, 3, 30)
    day_m_plus_1 = date(2026, 3, 31)
    tickers_30 = [f"T{i:02d}" for i in range(30)]

    early_events = [
        _filled_event(
            t, day_n, capital="1000.00", fill_date=day_n,
            settlement_date=day_m,
        )
        for t in tickers_30
    ]
    late_event = _filled_event(
        "TLATE", day_m_plus_1, capital="1000.00",
        fill_date=day_m_plus_1, settlement_date=date(2026, 5, 1),
    )

    open_time_map = _open_time_map(*tickers_30, "TLATE")
    out = apply_cap_layer(early_events + [late_event], open_time_map)

    # All 30 early posts admitted; the late post also admitted.
    assert sum(1 for e in out if e.outcome == OUTCOME_FILLED) == 31
    assert sum(1 for e in out if e.outcome == OUTCOME_BLOCKED_BY_CAP) == 0
    late_out = next(e for e in out if e.ticker == "TLATE")
    assert late_out.outcome == OUTCOME_FILLED


def test_capital_held_through_settlement_day():
    """A position with fill_date=N, settlement_date=M is still
    holding capital on day M. A new post on day M is therefore
    subject to the cumulative cap (this case tests fill_date < D <=
    settlement_date is the active condition)."""
    day_n = date(2026, 3, 1)
    day_m = date(2026, 3, 30)
    tickers_30 = [f"T{i:02d}" for i in range(30)]
    early_events = [
        _filled_event(t, day_n, capital="1000.00",
                      fill_date=day_n, settlement_date=day_m)
        for t in tickers_30
    ]
    # Post on day M itself: day-N capital still held (since M <= settlement)
    same_day_event = _filled_event(
        "TSAME", day_m, capital="1000.00",
        fill_date=day_m, settlement_date=date(2026, 5, 1),
    )
    out = apply_cap_layer(
        early_events + [same_day_event],
        _open_time_map(*tickers_30, "TSAME"),
    )
    # 30 early admitted; the day-M post is blocked because day-N
    # capital is still held on day M.
    same_out = next(e for e in out if e.ticker == "TSAME")
    assert same_out.outcome == OUTCOME_BLOCKED_BY_CAP


# ---------------------------------------------------------------------------
# Plan branch 3: tiebreaker — identical open_time → alphabetical
# ---------------------------------------------------------------------------


def test_tiebreaker_identical_open_time_alphabetical_by_ticker():
    """When two posts have identical open_time, the alphabetically
    earlier ticker is admitted first. With 31 posts where the LAST
    two share the same (latest) open_time, the alphabetically
    earlier of the two is admitted (#30) and the later is blocked
    (#31)."""
    pd = date(2026, 3, 1)
    sd = date(2026, 4, 1)
    # 29 posts with unique early open_times, then 2 posts with same late open_time
    tickers_29 = [f"T{i:02d}" for i in range(29)]
    tied_b = "TIE_B_TICKER"
    tied_a = "TIE_A_TICKER"  # alphabetically earlier
    open_time_map = _open_time_map(*tickers_29)
    # Both tied tickers share the same (latest) open_time
    open_time_map[tied_a] = "2026-01-01T00:00:00Z"
    open_time_map[tied_b] = "2026-01-01T00:00:00Z"

    events = [
        _filled_event(t, pd, fill_date=pd, settlement_date=sd) for t in tickers_29
    ] + [
        _filled_event(tied_b, pd, fill_date=pd, settlement_date=sd),
        _filled_event(tied_a, pd, fill_date=pd, settlement_date=sd),
    ]
    out = apply_cap_layer(events, open_time_map)

    # Total 31 candidates; 30 fit. The alphabetically earlier of the
    # two tied tickers (TIE_A) should be admitted; TIE_B blocked.
    a_out = next(e for e in out if e.ticker == tied_a)
    b_out = next(e for e in out if e.ticker == tied_b)
    assert a_out.outcome == OUTCOME_FILLED
    assert b_out.outcome == OUTCOME_BLOCKED_BY_CAP


def test_tiebreaker_open_time_first_then_ticker():
    """Tiebreaker is (open_time, ticker) — open_time wins even if
    ticker would suggest otherwise. Among 2 tied for cap admission,
    the one with EARLIER open_time wins, even if its ticker is
    alphabetically later."""
    pd = date(2026, 3, 1)
    sd = date(2026, 4, 1)
    tickers_30 = [f"T{i:02d}" for i in range(30)]
    open_time_map = _open_time_map(*tickers_30)
    # Now add two cap-conflict candidates: TZ_LATE_TICKER alphabetically
    # earlier but TA_EARLY_TICKER opened earlier.
    early_open = "TZ_OPENED_EARLIER"
    late_open = "TA_OPENED_LATER"
    open_time_map[early_open] = "2024-01-01T00:00:00Z"
    open_time_map[late_open] = "2026-01-01T00:00:00Z"

    events = [
        _filled_event(t, pd, fill_date=pd, settlement_date=sd) for t in tickers_30
    ] + [
        _filled_event(late_open, pd, fill_date=pd, settlement_date=sd),
        _filled_event(early_open, pd, fill_date=pd, settlement_date=sd),
    ]
    out = apply_cap_layer(events, open_time_map)

    # The earliest open_time should win. early_open is admitted; late
    # is blocked. Verify TA_OPENED_LATER (alphabetically earlier) does
    # NOT beat TZ_OPENED_EARLIER (open_time earlier).
    early_out = next(e for e in out if e.ticker == early_open)
    late_out = next(e for e in out if e.ticker == late_open)
    # 31 + 1 = 32 candidates total; cap allows 30. So one of the two
    # extras gets in; one of the 30 baseline gets blocked OR one of
    # the extras is blocked. Either way we want to see early_open
    # admitted (since its open_time is earliest of all).
    assert early_out.outcome == OUTCOME_FILLED
    # And late_open should be blocked (not winning over the others
    # which have unique open_times in 2025).
    assert late_out.outcome == OUTCOME_BLOCKED_BY_CAP


# ---------------------------------------------------------------------------
# Plan branch 4: cap counts only filled, not resting / cancelled
# ---------------------------------------------------------------------------


def test_cancelled_orders_dont_consume_cap():
    """5 cancelled posts on day 1 plus 30 filled posts on day 1 →
    all 30 filled admitted (the cancelled don't reserve capital)."""
    pd = date(2026, 3, 1)
    sd = date(2026, 4, 1)
    cancelled = [
        _cancelled_event(f"C{i:02d}", pd, OUTCOME_STALE, capital="1000.00")
        for i in range(5)
    ]
    filled = [
        _filled_event(f"F{i:02d}", pd, capital="1000.00",
                      fill_date=pd, settlement_date=sd)
        for i in range(30)
    ]
    open_time_map = _open_time_map(
        *[e.ticker for e in cancelled],
        *[e.ticker for e in filled],
    )
    out = apply_cap_layer(cancelled + filled, open_time_map)

    n_filled = sum(1 for e in out if e.outcome == OUTCOME_FILLED)
    n_stale = sum(1 for e in out if e.outcome == OUTCOME_STALE)
    n_blocked = sum(1 for e in out if e.outcome == OUTCOME_BLOCKED_BY_CAP)
    assert n_filled == 30  # all 30 fit
    assert n_stale == 5    # cancelled pass through unchanged
    assert n_blocked == 0


def test_out_of_zone_cancelled_orders_pass_through():
    """out_of_zone_cancelled events pass through as-is — they do
    not consume cap and are not blocked or filtered."""
    pd = date(2026, 3, 1)
    e = _cancelled_event("X1", pd, OUTCOME_OUT_OF_ZONE)
    out = apply_cap_layer([e], _open_time_map("X1"))
    assert len(out) == 1
    assert out[0].outcome == OUTCOME_OUT_OF_ZONE
    assert out[0].ticker == "X1"


# ---------------------------------------------------------------------------
# Plan branch 5: multi-outcome same-event same-day independence
# ---------------------------------------------------------------------------


def test_multi_outcome_same_event_same_day_both_attempted():
    """Two posts in the same multi-outcome event on the same day
    are evaluated independently. Both should fit if cap allows
    (no per-event grouping or restriction)."""
    pd = date(2026, 3, 1)
    sd = date(2026, 4, 1)
    e1 = _filled_event(
        "EVT-A", pd, fill_date=pd, settlement_date=sd,
        event_ticker="MULTIEVT", structure="multi-outcome-2-4",
    )
    e2 = _filled_event(
        "EVT-B", pd, fill_date=pd, settlement_date=sd,
        event_ticker="MULTIEVT", structure="multi-outcome-2-4",
    )
    out = apply_cap_layer([e1, e2], _open_time_map("EVT-A", "EVT-B"))
    assert all(e.outcome == OUTCOME_FILLED for e in out)
    # Both came from the same event_ticker; both admitted independently
    assert len({e.ticker for e in out}) == 2


def test_multi_outcome_same_event_subject_to_global_cap():
    """When the cap binds, multi-outcome same-event posts are
    blocked individually like any other post. No per-event
    exemption."""
    pd = date(2026, 3, 1)
    sd = date(2026, 4, 1)
    # 30 unrelated posts fill the cap
    fillers = [
        _filled_event(f"F{i:02d}", pd, fill_date=pd, settlement_date=sd)
        for i in range(30)
    ]
    # Two posts in the same multi-outcome event come AFTER the fillers
    # in tiebreaker order (later open_time)
    e1 = _filled_event(
        "MEVT-A", pd, fill_date=pd, settlement_date=sd,
        event_ticker="MULTIEVT", structure="multi-outcome-2-4",
    )
    e2 = _filled_event(
        "MEVT-B", pd, fill_date=pd, settlement_date=sd,
        event_ticker="MULTIEVT", structure="multi-outcome-2-4",
    )
    open_time_map = _open_time_map(*[f.ticker for f in fillers])
    open_time_map["MEVT-A"] = "2026-12-01T00:00:00Z"  # late
    open_time_map["MEVT-B"] = "2026-12-02T00:00:00Z"  # later

    out = apply_cap_layer(fillers + [e1, e2], open_time_map)
    # The fillers fit; both multi-outcome posts blocked
    a_out = next(e for e in out if e.ticker == "MEVT-A")
    b_out = next(e for e in out if e.ticker == "MEVT-B")
    assert a_out.outcome == OUTCOME_BLOCKED_BY_CAP
    assert b_out.outcome == OUTCOME_BLOCKED_BY_CAP


# ---------------------------------------------------------------------------
# Misc / determinism
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty():
    out = apply_cap_layer([], {})
    assert out == []


def test_output_length_matches_input_length():
    """Whatever the cap decisions, the cap layer never adds or drops
    events — every input event has exactly one output event."""
    pd = date(2026, 3, 1)
    sd = date(2026, 4, 1)
    events = (
        [_filled_event(f"F{i:02d}", pd, fill_date=pd, settlement_date=sd)
         for i in range(35)]
        + [_cancelled_event(f"C{i:02d}", pd, OUTCOME_STALE) for i in range(5)]
    )
    open_time_map = _open_time_map(*[e.ticker for e in events])
    out = apply_cap_layer(events, open_time_map)
    assert len(out) == len(events)


def test_idempotent_on_already_processed_events():
    """Running the cap layer on already-cap-processed events should
    produce the same outcomes (no event is double-counted because
    blocked_by_cap events have no capital_deployed contribution)."""
    pd = date(2026, 3, 1)
    sd = date(2026, 4, 1)
    tickers = [f"T{i:02d}" for i in range(31)]
    events = [
        _filled_event(t, pd, fill_date=pd, settlement_date=sd) for t in tickers
    ]
    open_time_map = _open_time_map(*tickers)
    out1 = apply_cap_layer(events, open_time_map)
    # blocked_by_cap events should remain blocked when re-processed.
    # filled events stay filled (cap math reproduces).
    out2 = apply_cap_layer(out1, open_time_map)
    o1 = sorted([(e.ticker, e.outcome) for e in out1])
    o2 = sorted([(e.ticker, e.outcome) for e in out2])
    assert o1 == o2
