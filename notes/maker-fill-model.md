# Maker fill model — v0.1 (locked)

**Status:** pre-registration document for Session B Stage 0. Locked before
the Test B simulator runs on settled-market data. Cannot be revised after
Test B's code begins running. Same discipline as `notes/investment-thesis.md`.

**Commit hash:** [to be filled at commit].

---

## 1. Purpose and scope

This document locks the rule that decides — from a daily candle alone —
whether a resting maker order at limit price `L` would have filled on a given
UTC day. The rule is one of four pre-registered inputs to Test B's pass
verdict: it directly affects the maker fill rate (≥30% pass condition,
thesis §6.6) and the count of filled positions `N` (≥30 pass condition).

Locking the rule **before** seeing simulator output is pre-registration.
Locking it **after** would be p-hacking: a permissive rule would inflate `N`
and fill rate above the pass thresholds; a conservative rule would suppress
both. Either revision after the fact selects the test's own outcome.

### Inputs

A single daily candle:
- `open`, `high`, `low`, `close` — USD prices, decimal strings, 4 decimals
  (e.g., `"0.1500"`), per the `_dollars` convention in `CLAUDE.md`.
- `volume` — count of contracts traded on that day.
- `open_interest` — used by the universe membership gate (thesis §2,
  ≥100 contracts), not by this rule.

A resting limit order:
- `side ∈ {sell-YES, buy-YES}`
- `limit_price L` — USD, 4 decimals
- `posted_at` — UTC timestamp at the moment of order placement (always at or
  immediately after a 00:00 UTC daily check, per thesis §2 data resolution)

### Output

For each UTC day on which the order is resting, the rule returns:
- `filled: bool`
- if filled: `fill_timestamp = candle close UTC` (since intra-day timing is
  not modeled at v0.1 daily resolution; thesis §2 explicitly accepts this).

The rule does **not** decide:
- queue position
- partial fills (all fills are full-size for v0.1)
- fill price ≠ `L`
- intra-day fill timing

---

## 2. The locked rule

```
def fills_on_day(candle, order) -> bool:
    """Return True iff order at limit price L fills on this UTC day."""
    if not is_resting_on(order, candle.utc_day):
        return False

    L = order.limit_price
    if candle.low <= L <= candle.high and candle.volume > 0:
        return True
    return False


def is_resting_on(order, utc_day) -> bool:
    """An order is resting on a UTC day iff:
       - utc_day >= floor_utc_day(order.posted_at), AND
       - the order has not been filled on any prior day, AND
       - the order has not been cancelled prior to this day's check.

       Posting and the daily entry/cancel check both happen at 00:00 UTC,
       so the day of posting is the first day on which fill is possible.
    """
    ...


def cancel_check_at_00_00_utc(order, prior_candle, current_actionable_zone) -> bool:
    """Return True iff the order is cancelled at this 00:00 UTC daily check.

       Cancel conditions, faithful to thesis §3 at daily resolution:

       (a) Stale-time: order has been resting for >24 hours since posted_at.
           At daily resolution, this is satisfied at the second 00:00 UTC
           check after posting (i.e., on the calendar day after the day of
           posting if the order remained unfilled through that day).

       (b) Out-of-zone: the previous candle's close is outside the
           actionable zone for the order's side.
           - sell-YES order is out-of-zone if prior_candle.close > 0.15
           - buy-YES order is out-of-zone if prior_candle.close < 0.85
           If this condition is true at the 00:00 UTC check, the order is
           cancelled before the new day's fill evaluation begins.

       Either condition triggers cancellation; both are evaluated.
    """
    ...
```

**The locked rule is rule (a)-modified: touch with volume guard.**
- sell-YES at `L` fills on a resting day iff `low ≤ L ≤ high AND volume > 0`.
- buy-YES at `L` fills on a resting day iff `low ≤ L ≤ high AND volume > 0`.
- Ties at exact `L` (i.e., `L == low` or `L == high`) count as fills.
- Fill timestamp is the candle close in UTC.

The cancel-and-replace overlay from thesis §3 is approximated at daily
resolution as: **cancel on the next 00:00 UTC daily check if the previous
candle's close is outside the actionable zone.** This frames the rule
around the once-per-day check rather than a continuous duration, which is
the faithful daily-resolution approximation of the thesis's "2 continuous
hours" wording. The 24-hour timer is similarly evaluated at the once-per-day
check.

---

## 3. Rationale (why this rule, not the alternatives)

Pure touch (`low ≤ L ≤ high`, no volume condition) overstates fills on
stale-print days at the longshot/favorite extremes. A market quoted at 88¢
with no trading activity that day will frequently print
`low = 88, high = 88, volume = 0`, mechanically satisfying the touch
condition without any actual counterparty crossing. The volume guard
eliminates that class of false positives.

The cross rule (`high ≥ L AND low < L AND volume > 0`) is too strict for
this universe and would reject genuine fills where the candle range
straddles `L`. At the longshot/favorite extremes the bid-ask spread is
often a single cent and price action concentrates near the touched extreme
of the day — a strict cross requirement would systematically understate
real fills at small `N`.

v0.1 errs permissive, consistent with the ≥T-bill pass threshold in thesis
§6.6 (which is itself calibrated to "not throw away real edges" at small
sample size). Phase 1b's live-shadow data will reveal whether
touch-with-volume matches reality, at which point the rule may be revisited
for a future test — but **not for v0.1 Test B**.

---

## 4. Acknowledged limitations

- **Queue position is not modeled.** A real maker order at the back of a
  long queue at price `L` may not fill even on a day where the candle
  satisfies the rule above. v0.1 treats every qualifying day as a fill;
  Phase 1b's live-shadow trading will produce empirical fill-rate evidence
  that the simulator can be calibrated against in a future revision.
- **Same-side trade volume is not used.** The volume guard counts total
  candle volume, not specifically volume that traded on the order's side.
  At the price extremes this is conservative in one direction (a sell-YES
  order at 15¢ may "fill" on a day where all volume actually crossed the
  bid at 14¢, never touching the offer) and permissive in the other.
  Refining this requires intra-day data.
- **Intra-day price path is not modeled.** A wick that touches `L` for
  one tick during the day is treated identically to `L` being the close.
  Some of those touches reflect transient sweeps where a real resting maker
  order would not have been at the front of the queue.
- **The 2-hour out-of-zone cancel rule is approximated as one-day-out-of-
  zone.** Thesis §3's "2 continuous hours" is finer-grained than daily
  resolution permits. The daily-check approximation may understate fills
  at fast price oscillation across the zone boundary, which is an
  acknowledged limitation, not a defect (thesis §2 explicitly accepts
  daily resolution for v0.1).
- **`volume = 0` days never fill.** This is intentional under the volume
  guard. A market with sustained zero-volume days at the actionable price
  produces zero fills, which is the honest daily-resolution conclusion
  even if a real maker might have caught a single isolated counterparty.

This rule is replaceable in Session C with intra-day data
(PredictionMarketBench-style replay or similar). It is locked for v0.1.

---

## 5. Lock statement

This fill model cannot be revised after Test B's code begins running on
settled-market data. The same pre-registration discipline applies as to
the investment thesis: revisions made *before* the run are legitimate;
revisions made *after* are p-hacking and disqualify the test. If the
simulator surfaces a result that requires a parameter change to look good,
the result is the answer — the change is not.
