# Test B simulator design — v0.1 (locked)

## 1. Status / lock statement

**Status:** pre-registration document for Session B Stage 2a. Locked
before the Stage 2b simulator code begins running on the locked
universe table. Cannot be revised after Stage 2b's simulator runs.
Same discipline as `notes/investment-thesis.md` and the prior Stage 0
and Stage 1b lock documents.

This document specifies the seven decisions that the simulator
implementation must make and that are not already locked elsewhere.
Decisions locked elsewhere — fill rule, cancel rule, daily-check
timing, voided-market detection, universe membership — are referenced
here, not re-stated.

This document depends on, and inherits all locks from:

- `notes/investment-thesis.md` (v3.3, §3 strategy spec, §4 capital
  management, §6 verdict conditions)
- `notes/maker-fill-model.md` (touch + volume>0 fill rule;
  cancel-on-next-00:00-UTC-check evaluated against the previous
  candle's close out-of-zone)
- `notes/candle-data-probe.md` (§1 endpoint, §5 ET-bucket date-label
  convention; the simulator's "daily check" fires at candle
  finalization, 04:00 UTC EDT / 05:00 UTC EST)
- `notes/voided-market-detection.md` (T-bill-over-lockup return
  attribution rule for confirmed-voided markets)
- `notes/universe-construction.md` (the 98-market locked universe and
  per-row schema)

If any of these upstream docs is revised, this document must be
re-evaluated before the simulator runs. They are not expected to be
revised.

**Commit hash:** [to be filled at commit].

---

## 2. Inputs

The simulator consumes three data sources and no others:

- **`notes/test-b-universe.csv`** — 98 markets (Stage 1b output, commit
  `f8e15ae`). The simulator iterates over these rows; per the §7
  limitation #5 of `universe-construction.md`, the universe is fixed
  at this snapshot and is not rebuilt.
- **The Kalshi candlesticks endpoint** — `GET /series/{series_ticker}
  /markets/{ticker}/candlesticks` per `notes/candle-data-probe.md` §1,
  authenticated via the same RSA-PSS pattern used by
  `scripts/build_test_b_universe.py` and `scripts/discover_universe.py`,
  with `period_interval=1440` (daily). Per universe-construction §2.7
  every market in the locked universe is reachable from this endpoint.
- **FRED API for T-bill rates** — series `DGS3MO` (Federal Reserve
  H.15, daily, 3-month Treasury Bill constant-maturity rate). See §3.4
  for the locked source URL and lookup convention.

No DB access (`DATABASE_URL` is not read). No portfolio endpoints. No
order placement.

---

## 3. The seven pre-registered simulator decisions

Each decision below states the question, the locked answer, the
rationale, and the alternative considered and rejected. The locked
answers cannot be revised after Stage 2b's simulator code runs.

### 3.1 Position sizing

**Question.** What does the thesis §4 "$1,000 per position" cap mean
operationally — $1,000 of notional capital, or 1,000 contracts?

**Locked.** Deploy $1,000 of notional capital per position.
- For a sell-YES position at limit price `L`, sell `floor(1000 / L)`
  contracts.
- For a buy-YES position at limit price `L`, buy `floor(1000 / L)`
  contracts.
- The `floor()` ensures integer contract counts.
- `capital_deployed = contracts * L` for both sides.

**Rationale.** Matches the natural reading of thesis §4's "per-position
cap" wording. Symmetric across sides at the notional level. Consistent
with the $30K total deployed-capital cap also in §4 — under this
convention, the cap admits at most 30 simultaneous filled positions
regardless of price level.

**Alternative rejected.** 1,000 contracts per position. Rejected
because it makes the $30K cap nearly meaningless across the price
range (1,000 contracts at L=0.05 deploys $50; 1,000 contracts at
L=0.95 deploys $950) and does not match a "per-position cap" intent.

### 3.2 Total capital cap

**Question.** Does "deployed capital" include resting unfilled orders,
or only filled positions?

**Locked.** Deployed capital includes filled positions only. Resting
unfilled orders do not reserve capital. The $30K cap is checked at
the moment of attempted post:
```
if (current_filled_capital + 1000) > 30000:
    block_post(reason="cap")
```

**Multi-post tiebreaker.** When multiple posts qualify on the same
daily check and admitting all of them would exceed the cap, the
simulator admits in deterministic priority order per thesis §4:
1. Earliest `open_time` first.
2. Ties broken alphabetically by `ticker`.
The simulator admits posts in this order until adding the next post
would exceed $30K, at which point remaining qualifying posts are
recorded as `outcome=blocked_by_cap`.

**Rationale.** Resting orders that don't fill incur no real
opportunity cost in the test. Reserving capital against them would
understate the strategy's effective utilization. The simulator's job
is to capture what the strategy would actually have committed.

**Alternative rejected.** `deployed = filled + resting`. Rejected
because resting orders are continuously cancellable (per the §3
cancel rule in `maker-fill-model.md`) and don't represent real
capital lockup.

### 3.3 Re-entry cooldown after cancel

**Question.** After a stale-cancel or out-of-zone-cancel on day N,
how soon can a new post be attempted on the same market?

**Locked.** No cooldown. After a cancel on day N, a new post on day
N+1's daily check is permitted if the standard entry conditions are
met. The next-day-or-later constraint is the natural rhythm of the
once-per-day check itself.

**Rationale.** The thesis is silent on cooldown; adding one would be
a layered judgment call without thesis justification. The cancel rule
already imposes the natural cadence of the once-per-day check.
Compounding restrictions without thesis backing risks under-exposing
the strategy to its own actionable zones.

**Alternative rejected.** 1-day or 7-day cooldown post-cancel.
Rejected as ad-hoc; no thesis basis.

### 3.4 T-bill rate source

**Question.** Where does the simulator read T-bill rates for the
verdict hurdle and for voided-market return attribution?

**Locked.** Per-fill-date 3-month Treasury Bill rate from FRED series
`DGS3MO` (Federal Reserve H.15, daily). The rate at the candle's
calendar-date label (per the ET-bucket convention in
`notes/candle-data-probe.md` §5) is the rate used for that fill's
hurdle and for any voided-market lockup-period return attribution.

**Source.** `https://fred.stlouisfed.org/series/DGS3MO` — public
endpoint, no authentication required for CSV download. The simulator
caches the FRED CSV at `/tmp/dgs3mo.csv` and looks up rates by date,
forward-filling for weekends and federal holidays per FRED's standard
convention (FRED returns blank cells on non-business days; the
simulator uses the most recent prior business-day rate).

**Units.** DGS3MO is published as a percentage (e.g., `5.27` means
5.27% simple-annualized). The simulator parses to decimal (`0.0527`)
before use.

**Rationale.** Per-fill-date matches the natural interpretation of
"T-bill" as "what could the deployed capital have earned if invested
in T-bills during this position's holding period." The chosen rate
applies for the whole holding period of that one position; this
treats each position's hurdle as locked at fill, consistent with how
a real T-bill investment over a comparable horizon would lock at
purchase.

**Alternative rejected.** Static T-bill rate (mean over the test
period). Rejected because rates moved meaningfully across 2024–2026
and a static rate would distort comparisons across positions filled
at different times. Particularly: positions filled in early 2024
(when rates were ~5.4%) and positions filled in late 2025 (when
rates were ~3.8%) would face artificially harmonized hurdles under a
static rate.

### 3.5 Annualized return formula

**Question.** Simple or compound annualization?

**Locked.** Simple annualization:
```
annualized_return = position_return * (365 / holding_period_days)
```
where `position_return = position_pnl_net_fees / capital_deployed`
(decimal) and `holding_period_days = (settlement_date - fill_date)`
in calendar days, with both dates as ET-bucket calendar dates per
the convention in `notes/candle-data-probe.md` §5.

**Rationale.** Simple annualization avoids compounding artifacts at
both ends of the holding-period spectrum. It matches how prediction-
market P&L is typically reported. The thesis §6.6 verdict condition
(median per-position annualized return ≥ T-bill) is meaningful under
simple annualization because T-bill rates (DGS3MO) are themselves
quoted as simple-annualized.

**Alternative rejected.** Compound annualization
`(1 + r)^(365/days) - 1`. Rejected because at very short holding
periods (e.g., a 7-day position with a 1% return compounds to ~67%
annualized) the values are not interpretable as expected returns
over a year of similar trades; they are arithmetic artifacts of the
formula. The median-of-annualized-returns metric in thesis §6.4 is
already noted there as designed to be robust to short-holding-period
extremes; simple annualization preserves that robustness, compound
annualization undermines it.

### 3.6 Multi-outcome same-event same-day entries

**Question.** If two markets in the same multi-outcome-2-4 event
independently satisfy entry conditions on the same daily check, are
both posts attempted, or at most one?

**Locked.** Independent. Both posts are attempted. The capital cap
(§3.2) and tiebreaker still apply across all candidate posts on a
given day, including across markets in the same event.

**Rationale.** The thesis universe explicitly includes
multi-outcome-2-4 events as legitimate sub-markets, and each market
within a multi-outcome event is a separate universe row in
`test-b-universe.csv`. Forbidding multi-entry would understate the
strategy's exposure to events with multiple actionable buckets and
would impose a constraint the thesis does not mandate.

**Rationale (continued).** The correlation issue is real but is
addressed at the analysis layer, not at the position-entry layer.
`notes/universe-construction.md` §7 limitation #1 documents the
correlation as a known limitation of treating event sub-markets as
independent for the Wilcoxon signed-rank test. Constraining at the
position level would address it inconsistently — it would suppress
some correlated pairs but not others, depending on which days the
entry conditions happened to fire.

**Alternative rejected.** At-most-one-per-event. Rejected because
the inconsistency above produces a position set that no longer
faithfully represents the v0.1 strategy's actual behavior on the
universe.

### 3.7 Fee model

**Question.** What fees apply at fill, and what is their formula?

**Locked.** Maker fees per Kalshi's official Fee Schedule PDF
(retrieved 2026-02-05; PDF "last updated and effective" date matches).
The v0.1 strategy posts resting limit orders only and therefore pays
maker fees, which are exactly 25% of the taker rate. Total fee per
filled position:

    total_fees = round_up_to_cent(0.0175 * contracts_attempted * L * (1 - L))

where `L` is the limit price as a decimal and `round_up_to_cent`
rounds to the next cent (Kalshi's stated convention). The fee is
charged once at fill, computed at the position level (total contracts
inside the parentheses), on both buy-YES and sell-YES positions.

**Per-contract values are artifacts.** Kalshi's rounding is at the
position level, so any per-contract figure derived as
`total_fees / contracts_attempted` is an artifact of the rounding,
not a real fee. The simulator does not compute or report per-contract
fees as a primary output; see §4 for the output-schema treatment.

**Worked example.** At `L = 0.10` and `contracts_attempted = 10000`
(v0.1 sizing for $1000 notional):

    raw = 0.0175 * 10000 * 0.10 * 0.90 = 15.75
    total_fees = round_up_to_cent(15.75) = $15.75

At `L = 0.85` and `contracts_attempted = 1176`:

    raw = 0.0175 * 1176 * 0.85 * 0.15 = 2.6243
    total_fees = round_up_to_cent(2.6243) = $2.63

At `L = 0.15` and `contracts_attempted = 6666`:

    raw = 0.0175 * 6666 * 0.15 * 0.85 = 14.8696
    total_fees = round_up_to_cent(14.8696) = $14.87

**v0.1 simplification: universal fee application.** Kalshi's PDF
states maker fees apply only to certain markets, not universally.
Whether each of the 98 universe markets falls in the fee-paying
section would require per-market verification. For v0.1 we treat all
98 markets as fee-paying — a conservative simplification that cannot
understate the strategy's net costs. Note that the special 0.035 fee
schedule that applies to certain S&P/NASDAQ markets is confirmed not
in scope: none of the 98-market universe carries `INX` or `NASDAQ100`
series prefixes.

**Rationale.** Matches the thesis §6 "net of maker fee" wording and
reflects the real cost a v0.1 strategy would incur. The strategy
operates at the price extremes (≤0.15 or ≥0.85) where the parabolic
fee curve `L*(1-L)` is small but non-zero; round-up-to-cent at the
position level handles the small-raw-fee case correctly without
introducing per-contract rounding artifacts. The fee schedule is
encoded as a function in the simulator, not as a constant — so that
future fee-schedule changes can be re-pointed without touching
simulator logic.

**Alternative rejected (1).** Zero fees or a flat per-trade fee.
Rejected because at the price extremes the parabolic fee is small but
non-trivial relative to per-position margins; pretending it does not
exist would inflate net returns systematically.

**Alternative rejected (2).** Per-contract fee computation followed
by sum (the original draft of this section, which used a `0.07`
coefficient — the taker rate — and rounded per-contract). Rejected
on two counts: the coefficient was wrong (taker, not maker), and
Kalshi's rounding is explicitly at the position level; per-contract
rounding would diverge from real Kalshi fees by up to ~$0.01 ×
contract_count in the worst case, which is meaningful at v0.1
contract counts (e.g., 10,000 contracts at L=0.10).

**Source.** Kalshi Fee Schedule,
https://kalshi.com/docs/kalshi-fee-schedule.pdf, retrieved
2026-02-05 (PDF "last updated and effective" date 2026-02-05).

---

## 4. Output schema — `notes/test-b-positions.csv`

Stage 2b's simulator writes a single CSV with one row per **attempted
post**. Both filled and unfilled posts are recorded; the `outcome`
column distinguishes them. Required columns, in this order:

| Column | Type | Meaning |
|---|---|---|
| `ticker` | string | Kalshi market ticker (carried from universe) |
| `event_ticker` | string | Carried from universe |
| `series_ticker` | string | Carried from universe |
| `primary_bucket` | string | One of the five thesis buckets, carried from universe |
| `structure` | string | `single-binary` or `multi-outcome-2-4`, carried from universe |
| `post_date` | string | ET-bucket calendar date of the attempted post (YYYY-MM-DD) |
| `side` | string | `sell-YES` or `buy-YES` |
| `limit_price` | decimal-string (4 dp) | The `L` the post was placed at, in `_dollars` format |
| `contracts_attempted` | integer | `floor(1000 / L)` |
| `capital_deployed` | decimal | `contracts_attempted * L` (dollars) |
| `outcome` | string | One of: `filled`, `stale_cancelled`, `out_of_zone_cancelled`, `blocked_by_cap`, `blocked_by_filter` |
| `fill_date` | string | ET-bucket calendar date of fill (YYYY-MM-DD); empty if not filled |
| `fill_price` | decimal-string | Equal to `limit_price` for v0.1 (maker orders fill at L); empty if not filled |
| `total_fees` | decimal | `round_up_to_cent(0.0175 * contracts_attempted * L * (1 - L))`; empty if not filled. Computed once at the position level (Kalshi rounds at fill, not per-contract) — see §3.7. Per-contract values are not output because they would be artifacts of the position-level rounding. |
| `settlement_date` | string | ET-bucket calendar date of settlement (YYYY-MM-DD); empty if not filled or if voided |
| `settlement_outcome` | string | `yes`, `no`, or `voided`; empty if not filled |
| `settlement_value_per_contract` | decimal | `1.0`, `0.0`, or empty for voided |
| `position_pnl` | decimal | Gross P&L in dollars; empty if not filled |
| `position_pnl_net_fees` | decimal | P&L net of `total_fees`; empty if not filled |
| `position_return` | decimal | `position_pnl_net_fees / capital_deployed`; empty if not filled |
| `holding_period_days` | integer | `(settlement_date - fill_date)` in calendar days; empty if not filled |
| `annualized_return` | decimal | `position_return * (365 / holding_period_days)`; empty if not filled |
| `tbill_rate_at_fill` | decimal | FRED `DGS3MO` rate on `fill_date`, expressed as simple-annualized decimal (e.g., `0.0527`); empty if not filled |

Encoding: UTF-8, LF line endings. Sort order: `(ticker, post_date)`
ascending so re-runs produce byte-identical output given the same
inputs and same FRED snapshot.

For voided positions: `settlement_outcome = "voided"`,
`settlement_value_per_contract` is empty, `position_pnl` and
`position_pnl_net_fees` are computed per
`notes/voided-market-detection.md` §4 (T-bill over lockup period),
and `holding_period_days` reflects the lockup period (capped at the
earlier of void-announcement date or original expected settlement
date), per that document.

---

## 5. Pre-registered diagnostic outputs

Stage 2b must emit the following diagnostics alongside the CSV. All
are derivable from the CSV — they are pre-registered to ensure the
simulator emits them consistently.

**Funnel (counts of attempted posts).**
- Total candidate posts considered (rows in CSV)
- Number filled
- Number `stale_cancelled`
- Number `out_of_zone_cancelled`
- Number `blocked_by_cap`
- Number `blocked_by_filter` (fails universe-membership or
  effective-window check at post time, defensive)

**Per-bucket and per-structure fill-rate.** Two breakdowns:
- Fill-rate by `primary_bucket` (5 rows: macro, geopolitics,
  us_politics, us_political_appointment, policy_outcome_quantitative)
- Fill-rate by `structure` (2 rows: single-binary, multi-outcome-2-4)

Each row reports `(filled / attempted)` as a percentage and as a
fraction.

**Capital utilization over time.**
- Peak `capital_deployed` (max simultaneous-filled across the test
  period)
- Mean `capital_deployed` over the full test period (from earliest
  post to latest settlement)
- Number of daily-check days at the $30K cap (capital_deployed ≥
  $30,000)

**Voided-position count.** Number of filled positions whose
`settlement_outcome = voided`. Expected zero per
`notes/historical-depth-probe.md` §3 / `notes/voided-market-detection.md`
§3 (the voided-detection rule was expected to match zero markets in
the universe). A non-zero count is a signal to inspect the affected
markets manually.

These diagnostics are printed to stdout at end of run and saved
alongside the CSV in a sibling text file `notes/test-b-diagnostics.txt`.

---

## 6. Documented limitations

Carried forward from prior stages:

1. **Correlation across event sub-markets.** Carried from
   `universe-construction.md` §7 limitation #1. Multi-outcome-2-4
   events with multiple filled buckets contribute correlated
   positions to the Wilcoxon test, understating effective sample
   size. Decision §3.6 (independent multi-entry) does not address
   this; the limitation lives at the analysis layer.

2. **Empty-schedule scheduled-event filter.** Carried from
   `universe-construction.md` §7 limitation #2. The
   `effective_window_*` columns in `test-b-universe.csv` equal full
   contract lifespan; no scheduled events are excluded for v0.1.
   Phase 1b paper-shadow is the right stage to introduce a curated
   schedule.

3. **Universe lockdate at 2026-05-04.** Carried from
   `universe-construction.md` §7 limitation #3 and confirmed in
   `notes/historical-depth-probe.md` §5. Markets settled after
   2026-05-04 are not in the universe.

4. **The 19-single-binary primary-cell ceiling.** Carried from
   `universe-construction.md` §7 limitation #4 and confirmed by
   `notes/historical-depth-probe.md` §4 (the ceiling is an actual
   Kalshi data ceiling, not a script-window artifact). The primary
   cell may verdict INSUFFICIENT SAMPLE per thesis §6.9; this is a
   pre-registered legitimate outcome.

5. **Microsecond-precision settlement timestamps in
   `expected_settlement_time`.** Carried from
   `universe-construction.md` §7 limitation #6. Stage 2b operates on
   calendar dates per the ET-bucket convention; microseconds do not
   affect simulator logic.

New in this stage:

6. **DGS3MO as risk-free proxy.** The 3-month Treasury Bill rate is
   used as the simple-annualized hurdle for verdict comparison and
   for voided-market lockup return attribution. DGS3MO is a reasonable
   proxy for the maker's risk-free alternative but is not a precise
   opportunity cost — the maker could plausibly access higher-yielding
   short-duration cash equivalents (money-market funds, T-bill ETFs).
   The thesis §4 explicitly cites "T-bill / cash sweep" as the
   benchmark, treating these as equivalent. We accept this
   approximation without further refinement at v0.1.

7. **Simple annualization at very short holding periods.**
   `annualized_return = position_return * (365 / holding_period_days)`
   produces large values when `holding_period_days` is small. A 5-day
   position with a 1% return reports +73% annualized; this is
   arithmetically correct but should be interpreted with caution as a
   summary statistic. The thesis §6.4 specifies the **median** of
   annualized returns as the headline metric precisely because it is
   robust to these short-period extremes; the median is locked, the
   mean is reported as a diagnostic only.

---

## 7. Lock statement

This methodology cannot be revised after Stage 2b's simulator code
begins running on the locked universe table. The same pre-
registration discipline applies as to the investment thesis and the
prior Stage 0 / 1b lock documents: revisions made *before* the
simulator runs are legitimate; revisions made *after* are p-hacking
and disqualify the test. If a result requires a methodology change
to look good, the result is the answer — the change is not.

The Stage 2b simulator (path: `scripts/simulate_test_b.py` or
similar) is the mechanical implementation of the seven decisions
above plus the upstream-locked rules referenced in §1. Re-running
the simulator on the same inputs (universe CSV unchanged, FRED
`DGS3MO` snapshot frozen at run time, Kalshi candlesticks endpoint
returning the same data) produces a byte-identical
`notes/test-b-positions.csv` and identical diagnostics.
