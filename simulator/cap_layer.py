"""Cross-market capital-cap and tiebreaker layer for the Test B simulator.

Pure module — no I/O, no network, no filesystem reads. Inputs in,
events out.

Consumes per-market PostEvent streams from simulator/daily_check.py
(sub-stage 2b.3) and emits cross-market admission decisions:
- Filled events that fit under the $30K cap pass through unchanged.
- Filled events that exceed the cap on their post_date are relabeled
  as outcome=blocked_by_cap; all fill/settlement/P&L fields cleared.
- Cancelled events (stale_cancelled, out_of_zone_cancelled) pass
  through unchanged — cancelled orders did not consume capital and
  are not subject to the cap.

Locked rules implemented (cross-references in source):

- Cap policy (notes/simulator-design.md §3.2):
    Deployed capital includes filled positions only; resting unfilled
    orders do not reserve capital. Cap is checked at the moment of
    attempted post: if (current_filled + capital_deployed) > $30K,
    the post is blocked.
- Multi-post tiebreaker (notes/simulator-design.md §3.2):
    On the same daily check, candidate posts are admitted in
    deterministic priority order:
      1. Earliest open_time first.
      2. Ties broken alphabetically by ticker.
    Greedy admission until adding the next post would exceed the cap.
- Multi-outcome same-event same-day independence
  (notes/simulator-design.md §3.6):
    No event-level grouping. Two posts in the same multi-outcome
    event on the same day are evaluated independently against the
    universe-wide cap and tiebreaker.

Capital lifecycle (logical):
    A position with fill_date=F and settlement_date=S contributes
    capital_deployed to current_filled at every daily check D such
    that F < D <= S. At D=F the post has not yet filled (post is at
    start of day, fill is at end). At D=S+1 the position has settled
    and capital is released.

Same-day cumulative cap on the post date:
    Per the spec, "admitting all of them would exceed the cap"
    triggers the blocked_by_cap relabel. Today's already-admitted
    posts count against subsequent same-day admissions for the cap
    check, even though they do not technically fill until end of
    day. The spec treats admission as the cap-relevant moment.

PostEvent shape:
    Imported from simulator/daily_check.py (locked in sub-stage 2b.3).
    The cap layer must construct new PostEvent instances when
    relabeling because PostEvent is frozen.
"""
from __future__ import annotations

import dataclasses
from collections import defaultdict
from datetime import date
from decimal import Decimal

from simulator.daily_check import OUTCOME_FILLED, PostEvent

# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------

CAPITAL_CAP = Decimal("30000")
OUTCOME_BLOCKED_BY_CAP = "blocked_by_cap"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_blocked_by_cap(filled_event: PostEvent) -> PostEvent:
    """Construct a new PostEvent with outcome='blocked_by_cap' and all
    fill-time/settlement/P&L fields cleared. Carried-from-meta and
    post-time fields stay (they describe the attempted post, which
    is what the schema records)."""
    return dataclasses.replace(
        filled_event,
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


def _capital_active_at(
    commitments: list[tuple[Decimal, date, date]],
    check_date: date,
) -> Decimal:
    """Sum of capital_deployed for commitments active at check_date.
    A commitment is active iff fill_date < check_date <= settlement_date."""
    total = Decimal("0")
    for cap, fill_date, settlement_date in commitments:
        if fill_date < check_date <= settlement_date:
            total += cap
    return total


# ---------------------------------------------------------------------------
# Main entry: apply cap and tiebreaker across the full event stream
# ---------------------------------------------------------------------------


def apply_cap_layer(
    events: list[PostEvent],
    open_time_by_ticker: dict[str, str],
) -> list[PostEvent]:
    """Apply the cross-market $30K cap and the (open_time, ticker)
    tiebreaker. Returns a new list of PostEvents, same length as
    `events`, with some `filled` outcomes relabeled to `blocked_by_cap`.

    `events`: per-market PostEvent stream from simulator/daily_check.py.
        Each event has post_date, outcome, capital_deployed,
        fill_date (for filled), settlement_date (for filled), and
        ticker.
    `open_time_by_ticker`: mapping from ticker to the ISO-format
        `open_time` string from notes/test-b-universe.csv. Used only
        for the tiebreaker. Must contain every ticker that appears
        in `events`.
    """
    if not events:
        return []

    by_date: dict[date, list[PostEvent]] = defaultdict(list)
    for e in events:
        by_date[e.post_date].append(e)

    # commitments: (capital_deployed, fill_date, settlement_date)
    commitments: list[tuple[Decimal, date, date]] = []
    output: list[PostEvent] = []

    for post_date in sorted(by_date.keys()):
        # Capital already committed from prior days' fills, evaluated
        # at this day's daily check.
        current_filled = _capital_active_at(commitments, post_date)

        # Tiebreaker: (open_time ascending, ticker ascending).
        # If a ticker is missing from the open_time map (defensive),
        # fall back to a high-sort-key sentinel so it sorts last.
        def _key(e: PostEvent) -> tuple[str, str]:
            return (open_time_by_ticker.get(e.ticker, "￿"), e.ticker)

        candidates = sorted(by_date[post_date], key=_key)

        for e in candidates:
            if e.outcome != OUTCOME_FILLED:
                # Cancelled (stale or out-of-zone): no cap impact;
                # pass through unchanged.
                output.append(e)
                continue

            # filled candidate — apply the cap check
            if current_filled + e.capital_deployed > CAPITAL_CAP:
                output.append(_to_blocked_by_cap(e))
            else:
                output.append(e)
                current_filled += e.capital_deployed
                # Track this commitment for future days' cap evaluations.
                # fill_date and settlement_date are non-None on filled
                # events (per the 2b.3 contract).
                assert e.fill_date is not None
                assert e.settlement_date is not None
                commitments.append(
                    (e.capital_deployed, e.fill_date, e.settlement_date)
                )

    return output
