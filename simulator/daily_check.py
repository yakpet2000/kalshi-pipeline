"""Per-market daily-check engine for the Test B simulator.

Pure module — no I/O, no network, no filesystem reads. Inputs in,
events out.

This module implements the per-market portion of the Test B
simulation logic: walking through a market's daily candles, deciding
when to post resting maker orders, evaluating fills and cancellations,
and producing PostEvent records that the cap-layer (sub-stage 2b.4)
will then admit-or-block across the universe.

Locked rules implemented here, all referenced from upstream lock
documents (cannot be revised after Stage 2b.7's integration run):

- Zone definition (notes/investment-thesis.md §3):
    sell-YES when yesterday's close <= 0.15 (longshot zone)
    buy-YES when yesterday's close >= 0.85 (favorite zone)
    L is yesterday's close.
- Daily check fires at 00:00 UTC of each ET-bucket day; ET-bucket
  date is the calendar-date label (notes/candle-data-probe.md §5).
- Fill rule (notes/maker-fill-model.md §2): low <= L <= high AND
  volume > 0; ties at exact L count as fills.
- Cancel rule (notes/maker-fill-model.md §2): at the next 00:00 UTC
  daily check, the order is cancelled. The reason is
  out_of_zone_cancelled if the prior candle's close is outside the
  actionable zone for the order's side, otherwise stale_cancelled.
  The order rests for at most one trading day (post on day N, fill
  or cancel by 00:00 UTC of day N+1).
- Position sizing (notes/simulator-design.md §3.1):
    contracts_attempted = floor(1000 / L)
    capital_deployed = contracts_attempted * L
- Fee model (notes/simulator-design.md §3.7):
    total_fees = round_up_to_cent(0.0175 * contracts * L * (1 - L))
- P&L:
    sell-YES: pnl_per_contract = L - settlement_value
    buy-YES:  pnl_per_contract = settlement_value - L
- Annualization (notes/simulator-design.md §3.5):
    annualized_return = position_return * (365 / holding_period_days)

The PostEvent dataclass shape locked here is the per-market
contract that sub-stage 2b.4 (cap layer) and 2b.6 (output writer)
consume. Cap layer may relabel `filled` to `blocked_by_cap` for
events that exceed the $30K cap. Voided settlement handling is
deferred to sub-stage 2b.5.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, Decimal
from typing import Any, Callable

from simulator.et_bucket import et_bucket_date

# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------

LONGSHOT_ZONE_HIGH = Decimal("0.15")  # sell-YES posts iff close <= 0.15
FAVORITE_ZONE_LOW = Decimal("0.85")   # buy-YES posts iff close >= 0.85
NOTIONAL_PER_POSITION = Decimal("1000")
FEE_COEFFICIENT = Decimal("0.0175")
ANNUAL_DAYS = Decimal("365")

SIDE_SELL_YES = "sell-YES"
SIDE_BUY_YES = "buy-YES"

OUTCOME_FILLED = "filled"
OUTCOME_STALE = "stale_cancelled"
OUTCOME_OUT_OF_ZONE = "out_of_zone_cancelled"

# Settlement outcomes
SETTLE_YES = "yes"
SETTLE_NO = "no"
SETTLE_VOIDED = "voided"


# ---------------------------------------------------------------------------
# PostEvent: per-market output contract for downstream sub-stages
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostEvent:
    """One attempted post on one market. Sub-stage 2b.4 may relabel
    `outcome` from `filled` to `blocked_by_cap`. Voided handling
    is deferred to 2b.5."""

    # Carried from market_meta
    ticker: str
    event_ticker: str
    series_ticker: str
    primary_bucket: str
    structure: str

    # Post-time fields
    post_date: date
    side: str
    limit_price: Decimal
    contracts_attempted: int
    capital_deployed: Decimal

    # Outcome (per-market view)
    outcome: str

    # Fill-time fields (None if not filled)
    fill_date: date | None
    fill_price: Decimal | None
    total_fees: Decimal | None

    # Settlement fields (None if not filled or not yet handled)
    settlement_date: date | None
    settlement_outcome: str | None
    settlement_value_per_contract: Decimal | None

    # P&L fields (None if not filled)
    position_pnl: Decimal | None
    position_pnl_net_fees: Decimal | None
    position_return: Decimal | None
    holding_period_days: int | None
    annualized_return: Decimal | None
    tbill_rate_at_fill: Decimal | None


# ---------------------------------------------------------------------------
# Candle accessors
# ---------------------------------------------------------------------------


def _close_or_previous(candle: dict) -> Decimal | None:
    """Return the candle's close price as a Decimal. Falls back to
    `previous_dollars` if `close_dollars` is absent (zero-volume
    days have no close but may carry rolled-forward `previous`).
    Returns None if neither is present."""
    price = candle.get("price") or {}
    for key in ("close_dollars", "previous_dollars"):
        v = price.get(key)
        if v not in (None, ""):
            try:
                return Decimal(str(v))
            except Exception:
                continue
    return None


def _candle_fills(candle: dict, L: Decimal) -> bool:
    """Apply the locked fill rule: low <= L <= high AND volume > 0.
    Ties at exact L count as fills (per maker-fill-model.md §2)."""
    try:
        volume = Decimal(str(candle.get("volume_fp", "0") or "0"))
    except Exception:
        return False
    if volume <= 0:
        return False
    price = candle.get("price") or {}
    high_str = price.get("high_dollars")
    low_str = price.get("low_dollars")
    if high_str in (None, "") or low_str in (None, ""):
        return False
    try:
        high = Decimal(str(high_str))
        low = Decimal(str(low_str))
    except Exception:
        return False
    return low <= L <= high


# ---------------------------------------------------------------------------
# Position sizing, fees, P&L
# ---------------------------------------------------------------------------


def _round_up_to_cent(amount: Decimal) -> Decimal:
    """Round up to the next cent ($0.01) per Kalshi's stated convention
    (notes/simulator-design.md §3.7). Uses Decimal ROUND_CEILING."""
    return amount.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def compute_sizing(L: Decimal) -> tuple[int, Decimal]:
    """Return (contracts_attempted, capital_deployed) for a $1000
    notional position at limit price L. floor(1000 / L) per
    simulator-design.md §3.1."""
    quotient = NOTIONAL_PER_POSITION / L
    contracts = int(quotient)  # Decimal -> int truncates toward zero
    capital = Decimal(contracts) * L
    return contracts, capital


def compute_fee(contracts: int, L: Decimal) -> Decimal:
    """Maker fee per simulator-design.md §3.7. Computed once at fill
    on the position level."""
    raw = FEE_COEFFICIENT * Decimal(contracts) * L * (Decimal("1") - L)
    return _round_up_to_cent(raw)


def compute_pnl(side: str, L: Decimal, contracts: int,
                settlement_value: Decimal) -> Decimal:
    """Gross position P&L given the side, limit price L, contract
    count, and settlement value per contract."""
    if side == SIDE_SELL_YES:
        per_contract = L - settlement_value
    elif side == SIDE_BUY_YES:
        per_contract = settlement_value - L
    else:
        raise ValueError(f"unknown side: {side!r}")
    return per_contract * Decimal(contracts)


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------


def _make_post_meta(market_meta: dict, post_date: date, side: str, L: Decimal) -> dict:
    """Build the constant fields that all PostEvents from this market
    share (carried-from-meta plus post-time fields)."""
    contracts, capital = compute_sizing(L)
    return {
        "ticker": market_meta["ticker"],
        "event_ticker": market_meta["event_ticker"],
        "series_ticker": market_meta["series_ticker"],
        "primary_bucket": market_meta["primary_bucket"],
        "structure": market_meta["structure"],
        "post_date": post_date,
        "side": side,
        "limit_price": L,
        "contracts_attempted": contracts,
        "capital_deployed": capital,
    }


def _build_cancelled_event(post_meta: dict, outcome: str) -> PostEvent:
    """Construct a PostEvent for a cancelled order — no fill, no
    settlement, no P&L."""
    return PostEvent(
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
        **post_meta,
    )


def _build_filled_event(
    post_meta: dict,
    fill_date: date,
    settlement_date: date,
    settlement_outcome: str,
    settlement_value: Decimal | None,
    tbill_lookup: Callable[[date], Decimal],
) -> PostEvent:
    """Construct a filled PostEvent. Computes fee, P&L, holding-period,
    annualized return. Voided settlements (settlement_value=None) are
    not handled here — sub-stage 2b.5 adds T-bill-over-lockup
    attribution. For now we raise on voided to make the sub-stage
    boundary explicit."""
    if settlement_value is None:
        raise NotImplementedError(
            "voided settlement P&L is sub-stage 2b.5; daily_check does "
            "not handle settlement_outcome='voided'"
        )
    L = post_meta["limit_price"]
    side = post_meta["side"]
    contracts = post_meta["contracts_attempted"]
    capital = post_meta["capital_deployed"]

    total_fees = compute_fee(contracts, L)
    position_pnl = compute_pnl(side, L, contracts, settlement_value)
    position_pnl_net_fees = position_pnl - total_fees
    position_return = position_pnl_net_fees / capital
    holding_period_days = (settlement_date - fill_date).days
    if holding_period_days <= 0:
        # Same-day fill and settlement is theoretically possible if a
        # contract opens and settles on the same ET-bucket date, but
        # produces an undefined annualized return. Anchor to 1 day to
        # keep the formula well-defined; flag in the future via
        # diagnostics if it ever fires in production data.
        holding_period_days = 1
    annualized_return = position_return * (ANNUAL_DAYS / Decimal(holding_period_days))
    tbill_rate = tbill_lookup(fill_date)

    return PostEvent(
        outcome=OUTCOME_FILLED,
        fill_date=fill_date,
        fill_price=L,
        total_fees=total_fees,
        settlement_date=settlement_date,
        settlement_outcome=settlement_outcome,
        settlement_value_per_contract=settlement_value,
        position_pnl=position_pnl,
        position_pnl_net_fees=position_pnl_net_fees,
        position_return=position_return,
        holding_period_days=holding_period_days,
        annualized_return=annualized_return,
        tbill_rate_at_fill=tbill_rate,
        **post_meta,
    )


# ---------------------------------------------------------------------------
# Main entry: run one market through the daily-check loop
# ---------------------------------------------------------------------------


def _zone_side_for(close: Decimal) -> str | None:
    """Return SIDE_SELL_YES or SIDE_BUY_YES if close is in an
    actionable zone, else None."""
    if close <= LONGSHOT_ZONE_HIGH:
        return SIDE_SELL_YES
    if close >= FAVORITE_ZONE_LOW:
        return SIDE_BUY_YES
    return None


def _is_out_of_zone(side: str, close: Decimal) -> bool:
    """Per maker-fill-model.md §2 cancel rule: the order's side
    determines the out-of-zone test."""
    if side == SIDE_SELL_YES:
        return close > LONGSHOT_ZONE_HIGH
    if side == SIDE_BUY_YES:
        return close < FAVORITE_ZONE_LOW
    raise ValueError(f"unknown side: {side!r}")


def _settlement_value_from_outcome(outcome: str) -> Decimal | None:
    if outcome == SETTLE_YES:
        return Decimal("1.0")
    if outcome == SETTLE_NO:
        return Decimal("0.0")
    if outcome == SETTLE_VOIDED:
        return None
    raise ValueError(f"unsupported settlement_outcome: {outcome!r}")


def run_market(
    candles: list[dict],
    market_meta: dict,
    tbill_lookup: Callable[[date], Decimal],
) -> list[PostEvent]:
    """Walk the market's candles, emitting a PostEvent per attempted
    post (filled or cancelled).

    `candles` is a list of raw Kalshi candle dicts (as cached by
    scripts/fetch_candlesticks.py). `market_meta` must include:
      ticker, event_ticker, series_ticker, primary_bucket, structure,
      settlement_outcome ("yes" / "no" / "voided")
    `tbill_lookup` is a callable(date) -> Decimal returning the
    annualized T-bill rate at the fill date.

    Side effects: none. Pure function.
    """
    posts: list[PostEvent] = []
    if not candles:
        return posts

    # Sort by Unix end_period_ts so we process in chronological order
    candles_sorted = sorted(candles, key=lambda c: int(c["end_period_ts"]))

    settlement_date = et_bucket_date(int(candles_sorted[-1]["end_period_ts"]))
    settlement_outcome = market_meta["settlement_outcome"]
    settlement_value = _settlement_value_from_outcome(settlement_outcome)

    resting_post_meta: dict | None = None  # carries side, L, post_date, etc.

    for i, today in enumerate(candles_sorted):
        today_date = et_bucket_date(int(today["end_period_ts"]))
        yesterday = candles_sorted[i - 1] if i > 0 else None
        yesterday_close = _close_or_previous(yesterday) if yesterday else None

        # ---- Phase A: cancel any resting order from yesterday's post ----
        # Per maker-fill-model.md §2 the cancel triggers at "the next
        # 00:00 UTC daily check" after posting, which is today's check.
        # The order had yesterday's full trading day to fill (Phase C
        # of the prior iteration); if it didn't, we cancel here.
        if resting_post_meta is not None:
            side = resting_post_meta["side"]
            if yesterday_close is not None and _is_out_of_zone(side, yesterday_close):
                outcome = OUTCOME_OUT_OF_ZONE
            else:
                outcome = OUTCOME_STALE
            posts.append(_build_cancelled_event(resting_post_meta, outcome))
            resting_post_meta = None

        # ---- Phase B: maybe post a new order at today's daily check ----
        # Entry conditions: yesterday's close in actionable zone,
        # no current resting order. L = yesterday's close.
        if yesterday_close is not None and resting_post_meta is None:
            side = _zone_side_for(yesterday_close)
            if side is not None:
                resting_post_meta = _make_post_meta(
                    market_meta, today_date, side, yesterday_close
                )

        # ---- Phase C: did today's candle fill the resting order? ----
        if resting_post_meta is not None:
            L = resting_post_meta["limit_price"]
            if _candle_fills(today, L):
                posts.append(_build_filled_event(
                    resting_post_meta,
                    fill_date=today_date,
                    settlement_date=settlement_date,
                    settlement_outcome=settlement_outcome,
                    settlement_value=settlement_value,
                    tbill_lookup=tbill_lookup,
                ))
                resting_post_meta = None

    # ---- Post-loop: order still resting at end of data ----
    # The market reached settlement before the order filled or got the
    # next day's cancel check. Treat as stale_cancelled (no fill,
    # no position).
    if resting_post_meta is not None:
        posts.append(_build_cancelled_event(resting_post_meta, OUTCOME_STALE))

    return posts
