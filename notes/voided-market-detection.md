# Voided-market detection — v0.1 (locked)

**Status:** pre-registration document for Session B Stage 0. Locked before
the Test B simulator runs on settled-market data. Cannot be revised after
Test B's code begins running. Same discipline as `notes/investment-thesis.md`.
This document satisfies the explicit Session B requirement in thesis §2 (decision
#26 in Appendix B): voided-market detection logic must be documented and
committed before Test B runs.

**Commit hash:** [to be filled at commit].

---

## 1. Purpose

Thesis §2 includes voided/canceled markets in the universe on a best-effort
basis: "When a position would have been held in such a market, its return
is computed as the opportunity cost of the locked capital — i.e., the
position contributes a return equal to the T-bill rate over the lockup
period." This document locks both (a) the procedure for identifying voided
markets from Kalshi's public API and (b) the return-attribution rule that
applies once a market is so identified.

The thesis explicitly accepts that detection is "best-effort" and that the
public API may not surface voided markets cleanly. This document records
what the API does surface, what it doesn't, and how the test handles the
gap.

---

## 2. API surface — probe findings (2026-05-06)

The probe queried `https://api.elections.kalshi.com/trade-api/v2/markets`
with no authentication, using `httpx`. Raw outputs are not committed; they
were saved during drafting under `/tmp/kalshi_void_probe/` for auditability
during the writing of this document.

**Confirmed base URL:** `https://api.elections.kalshi.com/trade-api/v2/`.

### 2.1 Status filter values

The `status` query parameter accepts only:
- `unopened`, `open`, `closed`, `settled`

Sending `active`, `initialized`, `determined`, or `voided` as a filter
returns HTTP 400. There is **no public `voided` or `canceled` status
filter**.

### 2.2 Status field values on returned market objects

The `status` field on returned market objects takes the values
`initialized` (filter `unopened`), `active` (filter `open`), `determined`
(filter `closed`), and `finalized` (filter `settled`). Across a 2,000-row
settled sample, every row had `status = "finalized"`. No `voided`,
`canceled`, or other unusual status value was observed.

### 2.3 Result and settlement_value_dollars across 2,000 settled markets

| Field | Distinct values observed | Counts |
|---|---|---|
| `result` | `"yes"`, `"no"` | 695 yes / 1,305 no |
| `settlement_value_dollars` | `"0.0000"`, `"1.0000"` | 1,305 / 695 |
| `is_provisional` | `true`, `null` | 1,675 / 325 |
| `expiration_value` | `""`, `"66.00"` | 1,976 / 24 |

No row in the 2,000-market sample showed an unusual `result` value (e.g.,
empty string, `"void"`, `null`). No row showed a fractional
`settlement_value_dollars` (which would suggest partial credit / refund).

`is_provisional=true` is **not** a void indicator: it appears in 84% of
normally-settled markets that have a clean `result ∈ {"yes", "no"}` and
`settlement_value_dollars ∈ {"0.0000", "1.0000"}`. It reflects normal
settlement-timer state during the dispute window, not voiding.

### 2.4 Auditable sample tickers (from probe drilldowns)

- **Settled `result=no`, normal:**
  `KXMVESPORTSMULTIGAMEEXTENDED-S2026AA2C2E08A8E-617B20A85D4` —
  `status=finalized`, `result="no"`, `settlement_value_dollars="0.0000"`,
  `is_provisional=true`.
- **Settled `result=yes`, normal:**
  `KXMVECROSSCATEGORY-S20266C03A76B623-1136689497B` —
  `status=finalized`, `result="yes"`, `settlement_value_dollars="1.0000"`.
- **Closed/determined (pre-finalization):**
  `KXXRPD-26MAY0603-T2.1399` — `status=determined`, `result="no"`,
  `expiration_value="1.4264"`.

### 2.5 Per-market detail fields relevant to voiding

The `GET /markets/{ticker}` response includes (among others):
`status`, `result`, `expiration_value`, `expiration_value_dollars`,
`settlement_value_dollars`, `settlement_ts`, `settle_time`,
`settlement_timer_seconds`, `is_provisional`, `expected_expiration_time`,
`latest_expiration_time`, `expiration_time`, `close_time`.

There is **no field whose name or semantics specifically denote a void or
cancel event**. `settlement_ts` is the timestamp of finalization for
normally-settled markets; in a hypothetical voided market it would
presumably hold the void-announcement timestamp, but this is not confirmed
by the probe (no voided markets surfaced).

---

## 3. Detection rule (locked)

A settled market is treated as **voided** if and only if either of the
following holds on the Kalshi public API record at the time of test:

1. `result` is null, missing, or the empty string, **OR**
2. `settlement_value_dollars` is neither `"0.0000"` nor `"1.0000"`.

```
def is_voided(market_payload) -> Literal["voided", "settled-normal", "ambiguous"]:
    """Classify a settled market for Test B purposes."""
    if market_payload.get("status") != "finalized":
        return "ambiguous"   # not a settled market; should not reach this fn

    result = market_payload.get("result")
    sv = market_payload.get("settlement_value_dollars")

    if result is None or result == "":
        return "voided"
    if sv not in ("0.0000", "1.0000"):
        return "voided"
    if result in ("yes", "no") and sv in ("0.0000", "1.0000"):
        return "settled-normal"
    return "ambiguous"
```

Based on the probe, this rule is expected to match **zero markets** in the
Test B universe. It exists to honor §2's pre-registration requirement
and to handle the edge case if it appears; it is not expected to drive
results.

### 3.1 Ambiguous-case handling

Per thesis §2, the default for ambiguous cases is **exclude from the
universe.** Specifically, any market for which `is_voided` returns
`"ambiguous"` is dropped from the universe entirely (not counted toward
universe size, contributes zero positions to track totals).

The `"voided"` classification, in contrast, **keeps** the market in the
universe (it counts toward universe size for analytical purposes per §2)
but routes any hypothetical position through the return-attribution rule
in §4 below rather than through normal mark-to-settlement.

---

## 4. Return attribution for confirmed-voided markets

For any hypothetical position that would have been held in a market
classified as `"voided"`:

- **Return:** the position contributes a return equal to the T-bill rate
  over the lockup period.
- **Lockup period:** days from the hypothetical fill date to the
  **earliest** of (void announcement date, original expected settlement
  date), whichever comes first.

Where these come from on the API record:
- **Hypothetical fill date:** the simulator's recorded fill date for the
  position (per `notes/maker-fill-model.md`).
- **Void announcement date:** the closest available proxy is
  `settlement_ts` on the voided-market record. If `settlement_ts` is null
  or missing, fall back to the original expected settlement date below.
- **Original expected settlement date:** `expected_expiration_time` on
  the market record (or `expiration_time` if `expected_expiration_time`
  is null).

The `min(void_announcement_date, expected_settlement_date)` rule honors
the thesis's wording: capital is locked only until the *earlier* of when
Kalshi released it (void) or when the contract was due to settle anyway.

---

## 5. Documented gaps

- **No dedicated voided/canceled API surface.** Voided markets, if they
  exist on Kalshi, are not reliably surfaced through the public API.
  There is no `voided` status filter, no `voided` status value, no
  dedicated `voided` boolean field.
- **Post-settlement voiding is invisible.** If a market is voided
  *after* normal settlement (e.g., after a dispute), the public API may
  simply update `result` and `settlement_value_dollars` to the
  post-dispute values without flagging the change. Test B has no way to
  distinguish "originally settled this way" from "settled, then voided
  and reset" using public-API data alone.
- **No void-specific timestamp.** `settlement_ts` is reused for both
  normal finalization and (presumably) voiding. There is no field that
  separately records the void-announcement moment. The detection rule
  treats `settlement_ts` as a best-effort proxy.
- **Probe sample size.** The 2,000-row sample is recent and may not
  include older voided markets. The detection rule is designed to be
  null-safe (matches zero of the probed sample) and to surface voids
  if any appear in the Test B universe; we cannot confirm the rule's
  precision against true voided cases without observing one.

We accept these as unresolvable limitations of the public-API data
source for v0.1. Higher-fidelity void detection (e.g., scraping Kalshi's
rules-and-resolutions docs, or operating on authenticated endpoints) is
out of scope for v0.1 and is not in the Session B plan.

---

## 6. Lock statement

This detection procedure cannot be revised after Test B's code begins
running on settled-market data. The same pre-registration discipline
applies as to the investment thesis: revisions made *before* the run are
legitimate; revisions made *after* are p-hacking and disqualify the test.
If a result requires a parameter change to look good, the result is the
answer — the change is not.
