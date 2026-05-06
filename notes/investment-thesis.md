# Investment Thesis — v3.3

**Status:** pre-registration document, drafted before Test B. **Lock candidate.**
**Supersedes:** v3.2.3 (drafted internally), all prior versions back to v2.1.
**Author:** Peter Yakovlev.

**Revision note (v3.2.3 → v3.3):** This is a substantive revision driven by universe-sizing data, not by additional review concerns. A Path B scrape of 5,000 settled Kalshi events established that the qualifying-market universe contains approximately 18 single-binary and 146 individual markets within 2-4 bucket multi-outcome events (164 candidate markets total) — an order of magnitude smaller than the v3.2.3 design assumed. The 250-position primary-cell floor in v3.2.3 was therefore unmeetable.

The fundamental change in v3.3 is reframing Test B from a confirmatory falsifying test to a **hypothesis-formation exercise** that informs whether to graduate to live paper-shadow trading. Specific consequences: universe widened to single-binary + 2-4 bucket multi-outcome (5+ bucket events excluded based on walkthrough A.2 evidence of bias compression); pass conditions reduced from 8 to 4; T-bill+3pp threshold relaxed to ≥T-bill at this stage (the +3pp bar moves to Phase 1b paper-shadow, where it belongs); Phase 1c machinery cut entirely; sample-size floor dropped from 250 to 30. The discipline of pre-registration is preserved — rules are still locked before the run, no mid-test edits — but applied to the right kind of test.

The live advisory-shadow plan from earlier discussions is **deferred to Phase 1b** as a separate session. Session B is historical Test B only.

See Appendix B for the full decisions log including the v3.3 changes.

This document is the binding pre-registration for Phase 1 of the Kalshi favorite-longshot trading project. Decisions written here are locked in the sense that they cannot be revised after Test B's code begins running on settled-market data. Revisions made *before* the run are legitimate; revisions made *after* are p-hacking and disqualify the test.

The discipline is borrowed from clinical-trial pre-registration. The point is not that this document is correct on every point. The point is that whatever it says, it stays said until the test is over.

---

## 1. Thesis

The favorite-longshot bias documented by Bürgi, Deng, and Whelan (CEPR DP 20631) is a pattern of **systematic mispricing**: on Kalshi, longshot contracts (priced ≤15¢) are overpriced relative to their realized win rates, and favorite contracts (priced ≥85¢) are underpriced relative to theirs. The mispricing is driven by uninformed taker flow — taker participants pay more than fair value for longshots and accept less than fair value for favorites. The maker side of these flows is the structural beneficiary.

This thesis investigates whether the bias is capturable in long-dated macro and geopolitical contracts via maker-side execution — buying favorites at ≥85¢ and selling longshots at ≤15¢, holding to settlement. The qualifying universe in Kalshi's settled history is small (~150 candidate markets), which precludes a confirmatory statistical test at conventional power. Test B is therefore a **hypothesis-formation exercise**: its purpose is to determine whether observed strategy performance justifies graduating to live paper-shadow trading, where additional data will inform a real-money decision.

The bias is structural, not informational. The strategy does not require a fair-value model — it does not estimate probabilities or compete with the market's information aggregation. The strategy bets on a directional mispricing pattern that, if real, appears across many contracts rather than in any single one.

---

## 2. Universe definition

The strategy's tradeable universe is the set of Kalshi markets satisfying every condition below:

**Bucket inclusion.** Each qualifying market is assigned exactly one primary bucket from:
- macro
- geopolitics
- us_politics
- us_political_appointment
- policy_outcome_quantitative

Each market may carry zero or more secondary tags for sub-analysis only. Primary bucket is locked at universe entry; secondary tags do not count toward primary aggregates.

**Lifespan.** ≥30 days from market open to settlement.

**Liquidity.** Open interest at the moment of order-placement attempt ≥ **100 contracts.** This is evaluated at the timestamp the strategy attempts to post, not at any later point. Markets are not pre-filtered on lifetime peak OI; the constraint operates as a per-attempt gate, not a universe membership gate. If a market never reaches 100 OI during a moment when the price is in an actionable zone, the strategy never trades it — but the market remains in the universe for analytical purposes (it counts toward universe size, contributes zero positions to track totals).

**Data resolution.** All evaluations — price, OI, entry conditions, stale-cancel timers — are performed at end-of-day candle close, using the most recently finalized daily candlestick available at that moment. Specifically: the strategy checks for entry conditions once per UTC day at 00:00 UTC, immediately after the previous day's candle has finalized. The OI value used for the liquidity gate is the OI reported in that just-finalized candle. The price used to evaluate "actionable zone" membership is the close price of that candle.

Daily resolution is locked for v0.1 because it is what the public Kalshi API provides reliably for historical periods. The "whenever conditions are true" framing in earlier drafts has been replaced with "once per UTC day" to align with this resolution. Higher-resolution validation (intra-day timing, queue position) is deferred to Session C, potentially using PredictionMarketBench-style replay infrastructure. The daily-resolution choice may understate fill rates for very-short-duration entries; this is an acknowledged limitation, not a defect.

The 100-contract threshold is provisional. It was selected based on three pre-Session-B walkthrough markets (see Appendix A) where actionable-zone entries occurred at OI values ranging from 1 to 4,833 contracts. 100 is high enough to exclude single-poster early-life book states and low enough to admit most genuine market activity. **The threshold cannot be revised mid-test.** Post-test fill-rate diagnostics may inform a revision in a future test, but no v0.1 result may be re-evaluated against a different threshold.

**Resolution structure.** Each market is tagged at universe entry as one of:
- single-binary (one YES/NO question, one market in the event)
- multi-outcome 2-4 (event has 2-4 mutually exclusive markets)

Events with 5 or more mutually exclusive markets are **excluded** from the v3.3 universe. This exclusion is based on walkthrough A.2 (Costa Rica margin of victory, 9 buckets) which showed that high-cardinality multi-outcome events compress the favorite-longshot signal across buckets, making the bias structurally harder to detect. Excluding 5+ bucket events trades universe size for signal clarity. Universe-sizing data: 5+ bucket events are 65% of the long-dated in-universe event count (119 of 183 events) but contribute the bulk of "noise" markets where individual buckets sit in dead zones.

For multi-outcome 2-4 events, each individual market within the event is a separate universe candidate. A 3-market event contributes 3 candidate markets, each evaluated independently against the actionable-zone criteria.

**Scheduled-binary-event filter.** A market is excluded from its trading window for any period in which a scheduled event from the locked event-type list below falls within that window. The locked event-type list:

- Scheduled elections (federal, state, foreign-national, regional)
- Scheduled court ruling dates with announced decision schedules
- Scheduled Federal Reserve FOMC meeting dates
- Scheduled BLS official statistical releases (CPI, NFP, PPI, GDP) when the contract resolves on the result
- Scheduled treaty/legislation signing or ratification dates
- Scheduled official confirmation hearings or votes (Senate confirmation, parliamentary votes)
- Scheduled foreign government announcements with pre-published timing

Events not on this list do not trigger the filter, even if they would plausibly move the market. The filter is mechanical: present a market and a date range, filter operates without human judgment.

The strategy's effective trading window is the contract's life *minus* periods within ±3 calendar days of any listed event. If exclusion leaves no qualifying window of ≥30 days, the market is dropped from the universe entirely.

**Re-entry after filter window ends.** If a market is in an actionable zone at the moment the strategy exits a filtered window (i.e., the ±3 day exclusion period has just elapsed), and the strategy holds no position and no active order, an attempted post is placed immediately. The filter exclusion is binding only during its window; it does not disqualify the market from later trading.

Events discovered post-test (e.g., an election announced after the test runs) cannot trigger retroactive market exclusion. The list is locked at universe-construction time and applied uniformly.

**Stitched recurring-cycle markets.** Daily/weekly recurring contracts (oil settlement, Hormuz transit, etc.) are deferred to Phase 2. Phase 1 universe is one-shot markets only.

**Voided and canceled markets.** Markets that were voided, canceled, or otherwise resolved without normal settlement are included in the universe on a best-effort basis. When a position would have been held in such a market, its return is computed as the opportunity cost of the locked capital — i.e., the position contributes a return equal to the T-bill rate over the lockup period (which is functionally zero alpha). This is honest about the cost of capital lockup without overstating losses on disputed contracts.

The "best-effort" qualifier acknowledges that Kalshi's API may not surface voided markets cleanly through the `settled` status filter. To ensure the detection logic is non-discretionary at test time even though the exact rule cannot be specified in advance, Session B implementation must produce a `notes/voided-market-detection.md` document specifying:
- the exact API endpoints and status values used to identify voided markets,
- the handling rule for ambiguous cases (default: exclude),
- documented gaps where the API does not provide enough information to classify cleanly.

This document must be committed before Test B's code begins running on settled-market data. The detection logic, once locked in that document, cannot be revised after Test B's results are computed.

**Universe lockdate.** The universe is computed once, before the test runs, on all settled markets meeting the above criteria as of [date]. No markets are added or removed mid-test.

**Walkthrough exclusion.** The three markets analyzed in Appendix A — `KXNEXTIRANLEADER-45JAN01-MKHA` (Mojtaba Khamenei), `KXMOVCOSTARICAPRESR1-26FEB01` (Costa Rica margin of victory, all 9 buckets), and `KXCANADALIBERAL-26DEC31` (Canadian Liberals majority) — are excluded from the Test B sample. These markets informed the rule design (specifically the primary cell selection) and including them in the test would create a small look-ahead bias. The exclusion is mechanical: 11 markets removed from a universe expected to contain hundreds; immaterial to power.

---

## 3. Strategy specification — v0.1

**Posting rule.** The strategy posts maker-side limit orders only:
- **Sell-YES** when YES price is in the longshot zone (≤15¢)
- **Buy-YES** when YES price is in the favorite zone (≥85¢)

The strategy does not post in the dead zone (>15¢ and <85¢). Dead-zone time is real, intentional, and not a defect.

**Direction within zone.** When sell-YES and buy-NO are economically equivalent (true by construction since YES + NO = $1), the strategy posts on the side selected by the following deterministic rule applied in order:

1. **Tighter spread first.** Post on whichever side (sell-YES or buy-NO) has the narrower bid-ask spread at the moment of attempt. **Empty-side handling:** if either side has no resting bid or no resting ask, that side's spread is treated as $1.00 for comparison purposes (i.e., the worst possible spread). If both sides have undefined spreads (no bids or asks anywhere), proceed to rule 2.
2. **Larger displayed OI second.** If spreads are equal to the cent (or both undefined), post on the side with greater displayed OI on that side of the book.
3. **Sell-YES default.** If both spread and OI are equal across sides, default to sell-YES.

This rule replaces the v3.2.1 "sell-YES is the canonical action" framing, which was insufficiently specified for an implementer. The tighter-spread-first ordering reflects that the side with the narrower spread typically has more competitive making activity and shorter expected fill times, which dominates queue-position concerns in practice. The empty-side default to $1.00 ensures the rule is well-defined at the longshot/favorite extremes where one side of the book is often empty.

**Entry timing.** A post is attempted whenever the following conditions are simultaneously true:
- the market is in an actionable zone (price ≤15¢ for sell-YES or ≥85¢ for buy-YES),
- the strategy holds no current position in this market,
- the strategy has no active open order in this market,
- the market is in its effective trading window (not currently inside a ±3 day filter exclusion).

This rule replaces the earlier "first enters" formulation, which was ambiguous when prices oscillated in and out of the zone. Re-entry after a stale-cancel is permitted under this rule and counts as a new attempted post for fill-rate accounting.

**Stale-order cancellation.** An active resting order is canceled if either of the following occurs:
- the order has been unfilled for >24 hours since placement, **or**
- the market price has remained outside the actionable zone for >2 continuous hours since the order was placed.

The second condition addresses the scenario where price moves out of zone shortly after order placement. Leaving a sell-YES order at 15¢ resting after price has drifted to 25¢ is toxic — the bias mechanism no longer applies, and the order becomes adverse-selection bait. The 2-hour grace period absorbs short-term oscillation without leaving the order exposed during sustained drift.

Cancellation does not preclude re-entry: if the price returns to the actionable zone and the entry conditions above are again satisfied, a new attempted post is placed immediately.

**Holding rule.** Hold to settlement. No early exits, no profit-taking, no stop-losses. The only exits are settlement of the contract or cancel-and-replace of unfilled orders.

**Sizing.** Per-position cap fixed at **$1,000 nominal exposure** for all positions. No discretionary sizing variation. Total active deployed capital cap at $30,000. Capital not deployed is held in the brokerage's cash sweep or T-bill ETF and earns the benchmark rate; it is not counted as "idle" because it is earning the comparison benchmark.

Sizing variation is explicitly out of scope for v0.1 and may be considered for v0.2 only on the basis of Phase 1 results.

**Multi-entry/exit.** Explicitly out of scope for v0.1. Will be considered for v0.2 only if a fair-value model is developed, which is not in the Phase 1 plan.

---

## 4. Constraint layer

The deterministic constraint layer is evaluated on every potential trade, before any strategy decision. The layer is hard-coded rules, not strategy. A market or post that fails the layer never reaches the strategy logic.

The layer enforces:
- universe membership (section 2)
- price in actionable zone (section 3)
- not already holding a position in this market
- not within an excluded scheduled-binary-event period
- post would not exceed per-position cap
- total deployed capital after this post would not exceed $30K

Any violation aborts the post silently.

**Capital-allocation tie-breaker.** If multiple markets simultaneously satisfy all entry conditions on the same daily check and accepting all of them would exceed the $30,000 deployed-capital cap, the strategy admits markets in deterministic priority order:

1. **Earliest open date first.** Markets are ordered by their original `open_time` ascending — markets that have been open longer are admitted first.
2. **Alphabetical ticker second.** If multiple markets share an open date to the second, ordering is alphabetical by Kalshi ticker.

This rule is intentionally agnostic to OI, price, or any other variable that could correlate with the bias being tested. Using OI or extreme pricing as a tie-breaker would let the test pick its own winners — the deterministic mechanical ordering avoids that.

---

## 5. Why the bias should appear (theory)

Bürgi-Deng-Whelan find that taker flow on Kalshi is systematically biased: takers overpay for longshots (paying more than realized win rates justify) and underpay for favorites (accepting less than realized win rates justify). The maker side of these flows is the structural counterparty to that mispricing. A maker who buys favorites at the underpriced ≥85¢ and sells longshots at the overpriced ≤15¢ is harvesting the gap between *quoted prices* and *realized win rates* across many contracts.

Three claims are inherited from BDW and treated as background, not as conclusions Test B is required to defend:
1. The bias exists in the aggregate Kalshi sample.
2. The bias is structural (driven by uninformed taker flow), not informational.
3. The bias is replicable by a maker-side strategy operating on both extremes (favorites and longshots) of qualifying markets.

Test B's job is to detect whether claims 1 and 3 hold *in the universe specified in section 2*, which is a subset of the BDW sample. The cleanest interpretation of the data is that the bias may show up in some buckets/structures and not others — the three-track × three-category crosstab in section 6 is designed to surface that asymmetry rather than collapse it.

**External validity scope.** BDW's effect was measured on the aggregate Kalshi sample, which was dominated by sports and short-dated markets. The universe specified in this thesis is strictly long-dated macro and geopolitical contracts. There is a real possibility that institutional hedger flow on macro markets prices more efficiently than uninformed retail flow on sports markets, in which case the bias may be substantially weaker or absent in this universe. **A null result on Test B is informative — it tells us the bias does not generalize from BDW's sample to this universe — and does not refute BDW's published findings.** Test B is treated as a falsifying instrument for the *generalization claim*, not for the BDW effect itself.

---

## 6. Test B — measurement protocol

Test B is a **hypothesis-formation exercise**, not a confirmatory falsifying test. Its purpose is to determine whether observed strategy performance on settled Kalshi history justifies graduating to Phase 1b paper-shadow trading. Its purpose is *not* to confirm the bias exists at conventional statistical power — universe size precludes that. The pre-registration discipline still applies: rules locked before run, no mid-test revisions, no post-hoc cell selection. But the pass conditions are calibrated for "is this worth investigating further" rather than "is this real money ready."

### 6.1 Tracks

The test computes results on three tracks simultaneously, all pre-registered:

- **Track 1 (full):** all positions taken by v0.1 — both longshot-sell and favorite-buy halves combined.
- **Track 2 (favorite-buy only):** subset of v0.1 positions taken in the favorite zone (≥85¢).
- **Track 3 (longshot-sell only):** subset of v0.1 positions taken in the longshot zone (≤15¢).

### 6.2 Categories

Crossed with the three tracks are two structural categories from the universe tag:
- single-binary
- multi-outcome 2-4

This produces a 3×2 = 6-cell crosstab. (Multi-outcome 5+ events are excluded from the universe entirely, per section 2; they do not appear as a category here.) Every cell is pre-registered.

### 6.3 Primary cell vs descriptive cells

Exactly one cell is pre-registered as the **primary cell** and is the basis for the strategy's PASS/FAIL verdict.

**Primary cell:** Track 2 (favorite-buy only) × single-binary structure.

This is the cell with the strongest prior support based on three independent considerations:
- BDW's published asymmetry: makers earn small positive returns on favorites, modest losses on longshots.
- Walkthrough A.3 (Canadian Liberals): the favorite-buy half operated cleanly on a single-binary market with no shock risk.
- Walkthrough A.2 (Costa Rica): multi-outcome events showed compressed bias signal, motivating both the structural sub-categorization here and the exclusion of 5+ bucket events from the universe.

The other 5 cells are **descriptive** — they are reported alongside the primary cell, but they do not promote to PASS in v3.3. A descriptive cell that meets all primary-cell conditions in 6.6 is grounds to investigate that cell in Phase 1b paper-shadow, not grounds to declare strategy success on this run.

The Phase 1c forward-data follow-up testing apparatus from prior versions has been removed in v3.3. Once Test B is reframed as hypothesis-formation, "look at descriptive cells, pick the most promising one for live shadow" is the *correct* methodology, not data snooping. The post-test analysis path leads directly to Phase 1b paper-shadow on whichever cells look most promising; what passes Phase 1b's much higher bar (T-bill+3pp on live trading over 3+ months) is the actual deployment trigger.

### 6.4 Headline metric

**Median per-position annualized return, net of all Kalshi fees.**

For each filled position, compute the position's holding-period return on deployed capital, net of maker fee paid at fill, annualized using calendar days from fill to settlement. The cell-level metric is the **median** across positions in that cell.

The median is the headline metric (rather than the arithmetic mean) because annualizing returns over short holding periods can produce extreme values: a 1% gain on a 2-day trade annualizes to ~180%. A handful of such fast-settling positions in a 30-50 position sample would dominate an arithmetic mean and obscure whether most positions actually delivered the expected return. The median is robust to this skew.

The arithmetic mean of per-position annualized returns is reported as a diagnostic alongside the median. If mean and median diverge substantially, the divergence itself is information about the distribution. The arithmetic-mean view is also retained for forward iteration: as the model develops in v0.2 and beyond, mean-based diagnostics may become useful once the strategy is producing fewer outlier positions.

This metric is appropriate because idle capital — capital not currently deployed in a Kalshi position — is held in T-bill / cash sweep earning the benchmark rate. The dead-zone time is not penalized because it is not idle in the economic sense.

### 6.5 Diagnostic metrics (reported, not gating)

- p-value of the cell-level median against zero (Wilcoxon signed-rank test, Pratt's method for tie handling)
- Arithmetic mean of per-position annualized returns (skew indicator vs median)
- Sharpe ratio (per-position, annualized)
- Maximum drawdown
- Average post-fill 30-minute adverse price move (toxic-flow indicator)
- Average observed bid-ask spread *at the moment of order placement*
- Sample size per cell
- **Utilization:** total capital-days deployed in the primary cell ÷ total capital-days available (test period × $30,000). A primary cell that passes its conditions but has very low utilization (e.g., <5%) tells us the strategy works but is too thin to deploy meaningfully — that's information, not a failure.

### 6.6 Pass/fail thresholds — primary cell only

The primary cell passes if all of the following hold simultaneously:

| Condition | Threshold |
|---|---|
| Annualized return (median per-position, net of fees) | ≥ T-bill rate |
| Maker fill rate | ≥ 30% |
| Mean-vs-median sanity check | Arithmetic mean ≥ (median − 2 × standard error of median) |
| Sample-size floor | ≥ 30 filled positions |

The strategy as a whole **passes if and only if the primary cell passes all 4 conditions.** Descriptive cells passing or failing does not affect the verdict.

**Why ≥T-bill, not ≥T-bill+3pp.** The earlier v3.2.3 design used T-bill+3pp as the threshold. That bar is correct for a real-money deployment decision but wrong for a hypothesis-formation test on a small sample. With N≈30-50 positions, the standard error around any sample median is large enough that:

- A real edge of +5% above T-bill would fail the +3pp bar roughly 40% of the time by chance
- A non-edge (true median = 0%) would clear the +3pp bar roughly 15% of the time by chance
- That's the worst combination for what we're trying to learn

By contrast, ≥T-bill (i.e., median return positive against T-bill rate):

- A real +5% edge passes ~95% of the time
- A non-edge passes ~50% of the time

The ≥T-bill bar is intentionally permissive at v0.1. This is correct. Test B's job is to *not throw away real edges* with a tight threshold. The job of Phase 1b paper-shadow is to *catch the false positives* that pass v0.1 but don't replicate on live data. Phase 1b will use T-bill+3pp as the graduation threshold for real-money deployment; that is where the +3pp bar belongs.

This calibration is itself pre-registered. A reader who disagrees with this calibration should disagree before the test runs, not after.

**Mean-vs-median sanity check rationale.** The headline metric is the median (robust to short-duration annualization outliers), but the median can hide tail-loss exposure. If most positions earn small positive returns but a few catastrophic losses pull the mean far below the median, the strategy is bankrupting in real-money terms even though the median looks healthy. The sanity check requires that the arithmetic mean does not fall more than 2 standard errors of the median below the median itself. This catches the "median passes, mean is disastrous" failure mode without making the mean a co-equal headline metric.

**Sample-size floor of 30.** Universe-sizing data established that the primary cell will yield, at most, several dozen filled positions. The 30 floor is an honest minimum below which results are descriptive only — anything less than 30 filled positions cannot support even a hypothesis-formation conclusion. If the primary cell yields fewer than 30 filled positions, Test B reports "insufficient sample, descriptive only" and the strategy is not promoted to Phase 1b on the primary cell parameters. Other cells may still be evaluated for paper-shadow promotion if they meet 4-condition pass.

**Conditions deliberately not gating in v3.3.** The following were pass conditions in v3.2.3 and are reported as diagnostics in v3.3:

- T-bill+3pp threshold (moved to Phase 1b graduation)
- p < 0.05 statistical significance (under-powered at small N; informational only)
- Adverse-selection cap of 50 bps (informational; will gate Phase 1b)
- Spread cap of 4% (informational; will gate Phase 1b)
- Sample-size 250 (replaced with floor of 30)
- Recent-3-month decay sub-cut (under-powered at small N; reported only)

These are not abandoned permanently — they re-enter as pass conditions in Phase 1b paper-shadow, where larger live-data samples make them statistically meaningful.

### 6.7 Track 1 thesis-level transparency check

In addition to the primary-cell verdict, Test B reports the result for **Track 1 (full strategy, all included categories combined)** as a thesis-level transparency check. This is *not* a pass/fail condition and does not affect the primary-cell verdict. It is a reporting requirement.

If the primary cell PASSes but Track 1 returns ≤ 0% (i.e., the full v0.1 strategy as specified loses money in aggregate, even though the primary cell wins), the Test B output prominently reports:

> "PRIMARY CELL PASS — but the full v0.1 strategy as specified loses money in aggregate. The bias is captured in the primary-cell slice but is offset or absent across the broader strategy. Phase 1b paper-shadow should restrict to the primary-cell slice; the broader thesis (BDW bias generalizes across the full universe) is not supported by this test."

This requirement exists because the thesis (section 1) makes a generality claim about the BDW bias. A primary-cell-only pass is real evidence for the strategy in that slice, but is not evidence the bias is general across the broader universe. Honest reporting requires distinguishing these two cases.

### 6.8 Filled-vs-attempted semantics

The pass/fail thresholds operate on **filled positions only** for the return metrics, and on **attempted posts** for the fill rate. Both numbers are reported separately. A primary cell with strong filled returns but a fill rate below 30% fails the fill-rate condition and is treated as untradeable rather than as a winning strategy with execution problems.

### 6.9 Verdict ladder

- **PASS:** primary cell satisfies all 4 conditions in 6.6. → Phase 1b paper-shadow on the primary cell parameters; the +3pp graduation threshold and additional kill conditions apply at the Phase 1b stage, which is specified in a separate pre-registration document at the time Phase 1b runs.
- **DESCRIPTIVE PASS (no primary-cell pass, but at least one descriptive cell meets the 4 conditions):** Phase 1b paper-shadow runs on the descriptive cell that came closest to passing on each condition independently. Note: this is not a "promotion" of the descriptive cell to primary status; it is a pragmatic choice of which slice to paper-shadow. v0.1 strategy in the original primary-cell-defined form is abandoned.
- **INSUFFICIENT SAMPLE:** primary cell yields fewer than 30 filled positions. Test B reports descriptive results only; no PASS/FAIL verdict. Decision on whether to paper-shadow any cell defers to author judgment based on diagnostic output. v0.1 strategy may still be paper-shadowed on the cell with most signal, but this is acknowledged as below the formal pass bar.
- **FAIL:** no cell (primary or descriptive) meets the 4 conditions. Strategy v0.1 is abandoned. The thesis is reviewed if and only if Track 1 (full strategy) also shows median return ≤ 0; in that case the broader BDW-generalization claim has no support in this universe and the thesis itself is rethought.

Strategy-abandonment means the v0.1 specification doesn't capture the bias well enough to be worth running. Thesis-abandonment means the favorite-longshot bias either doesn't exist in this universe or isn't capturable by a maker-side strategy in any tractable form. The two are distinct.

### 6.10 Pre-registration commitments

I commit, before Test B's code begins to run on settled-market data:

- Not to revise any threshold in section 6.6.
- Not to change the primary cell designation in 6.3.
- Not to add or remove sub-tracks beyond the 6 cells specified.
- Not to redefine the universe in section 2 after the lockdate.
- Not to redefine the headline metric (median per-position annualized return, net of fees) in 6.4.
- Not to interpret a failing primary cell as success because some descriptive cell looks better, beyond the explicit DESCRIPTIVE PASS path defined in 6.9.
- Not to revise the OI-at-entry threshold, the scheduled-event list, or the cancel-and-replace timer values mid-test.
- Not to revise the data-resolution choice (daily candles for v0.1) mid-test.
- Not to revise the capital-allocation tie-breaker rule (earliest open date, alphabetical ticker) mid-test.
- Not to revise the direction-within-zone selection rule (tighter spread, larger OI, sell-YES) mid-test.
- Not to revise the empty-side spread default ($1.00) or the Pratt's-method tie handling for the Wilcoxon test mid-test.
- Not to revise the mean-vs-median sanity check threshold (mean ≥ median − 2 SE) mid-test.
- Not to add or remove markets from the walkthrough-exclusion list (the three Appendix A markets) mid-test.

If a result requires a parameter change to look good, the result is the answer. The change is not.

---

## 7. Capital, capacity, and progression

Phase 1 cap: $30,000 maximum total deployed at any time. Per-position cap: $1,000 nominal exposure (fixed).

Phase 1a runs on settled-market historical data only. No real money is at risk in Phase 1a.

Phase 1b runs paper-shadow on live markets following the rules locked above, conditional on Phase 1a verdict per section 6.10.

**Live-implementation deferred decisions.** Phase 1a runs against settled historical data where candle finalization is stable; Phase 1b runs against live-streaming data where API timing matters. The following implementation details are explicitly deferred to Phase 1b without modification of the thesis: API retry/buffer logic for missed candle indexing, retry intervals on transient connection failures, handling of intra-day exchange outages or partial settlements. These do not affect the validity of Phase 1a results and will be specified in a separate `notes/phase-1b-implementation.md` document before paper-shadow trading begins.

Phase 2 (real money) is conditional on Phase 1b replicating Phase 1a's verdict over 3+ months.

Capacity questions — whether the strategy can deploy meaningfully larger amounts of capital — are deferred to Phase 2. Phase 1's question is "does the bias work for this strategy on this universe," not "how much money can it make."

---

## 8. Risks and kill conditions

In addition to the kill conditions in 6.6 (which determine PASS/FAIL of Test B itself), the live-trading phases have additional risks:

- **Toxic flow on news shocks.** Pre-known shock dates are excluded by section 2. Unforeseeable shocks (Mojtaba-style — see Appendix A) remain a risk; the cancel-and-replace stale-order rule (section 3) is the structural mitigation.
- **Settlement disputes.** Markets where Kalshi's resolution is contested or delayed cost capital while pending. No mitigation in v0.1; tracked as a metric.
- **Liquidity collapse.** A market that meets the OI-at-entry criterion may have collapsed liquidity by the time the strategy posts. The fill-rate metric in 6.6 is the kill condition for this at v0.1.
- **Bias decay.** The recent-3-month sub-cut is reported as a diagnostic in v3.3 (under-powered for gating at small N). Decay re-enters as a kill condition in Phase 1b paper-shadow once the live-data sample is large enough to make the test meaningful.
- **Universe shift.** Kalshi may change market structure (new categories, fee changes, regulatory action). Material structural changes to Kalshi mid-test are grounds for pause-and-revisit, not for revising the test.

---

## 9. What this thesis is not

- It is not a claim that the favorite-longshot bias is large, or that it is easily monetizable. The expected returns are small on a per-position basis. The strategy's case rests on a large number of independent positions, not large per-position edges.
- It is not a claim that v0.1 is the best operationalization of the thesis. It is the simplest one. Layers will be added when specific failures justify them, not before.
- It is not a forecasting strategy. The strategy makes no probability estimates that compete with the market's.
- It is not a market-making strategy. The strategy does not turn over inventory. It posts and holds.

---

## 10. Success criteria

The strategy succeeds at Phase 1a if the primary cell (Track 2 × single-binary, per section 6.3) satisfies all 4 conditions of section 6.6, OR if any descriptive cell satisfies them under the DESCRIPTIVE PASS path defined in section 6.9. This is a hypothesis-formation pass: it justifies graduating to Phase 1b paper-shadow on the relevant cell parameters.

The strategy succeeds at Phase 1b paper-shadow if the cell carried forward replicates its Phase 1a directional signal over 3+ months of live shadow trading, against the Phase 1b conditions (which include the T-bill+3pp threshold and the additional kill conditions deferred from v3.3). Phase 1b is specified in a separate pre-registration document at the time it runs.

The strategy succeeds at Phase 2 (real money) if Phase 1b's paper-shadow result holds when small real-money positions are placed, evaluated over 3+ months. Phase 2 is conditional on Phase 1b's pass.

The thesis succeeds if any of Phase 1a, Phase 1b, or Phase 2 produces a PASS through the paths above. The thesis fails if Phase 1a fails AND Track 1 (full strategy) shows median return ≤ 0; in that case the broader BDW-generalization claim has no support in this universe and the thesis itself is rethought. Strategy-abandonment ≠ thesis-abandonment: a v0.1 specification can fail while the thesis remains testable under a different operationalization.

---

## Appendix A — Walkthrough validation

Three settled markets were walked through in pre-registration to validate that the rules in this document are operationally well-defined. The walkthroughs are not data points and inform priors only — they do not appear in Test B's sample (see section 2 walkthrough exclusion).

**A.1 Mojtaba Khamenei (KXNEXTIRANLEADER-45JAN01-MKHA).** Geopolitics, single-binary, 62 days, settled YES. Price spent ~50 days at 13–25¢ then jumped to 73¢ on Mar 4, 2026 after the death of Ali Khamenei. **Motivated:** the cancel-and-replace stale-order rule (section 3, 24-hour and 2-hour out-of-zone timers) — a resting longshot order would have been toxically filled at the news shock without these.

**A.2 Costa Rica margin of victory (KXMOVCOSTARICAPRESR1-26FEB01).** Geopolitics, 9 mutually-exclusive buckets, election Feb 1 2026. **Motivated:** (1) the scheduled-binary-event filter (section 2) — the election was a known event mid-trading-window that broke hold-to-settlement assumptions; (2) the exclusion of 5+ bucket multi-outcome events from the universe (section 2) — high-cardinality events compress the bias signal, with most buckets sitting in dead zones throughout.

**A.3 Canadian Liberals majority (KXCANADALIBERAL-26DEC31).** Geopolitics, single-binary, 95 days, settled YES. Price drifted 33¢ → 92¢ over three months without scheduled-event shocks. A buy-YES post at 91¢ in mid-March would have earned ~9¢ per contract over 34 days, net of 0.16¢ maker fee. **Motivated:** the choice of favorite-buy × single-binary as the primary cell (section 6.3) — this was the cleanest example of v0.1 capturing the bias as theorized.

---

## Appendix B — Locked decisions log

The following decisions are locked in this version of the thesis:

| # | Decision | Source |
|---|---|---|
| 1 | OI-at-entry minimum threshold = 100 contracts (provisional), evaluated at daily-candle resolution | v3.1, Gemini v3.0 concern 1; data resolution added in v3.2, Gemini v3.1 concern 8 |
| 2 | Scheduled-binary-event filter, deterministic event-type list | v3.0, A.2; tightened in v3.1, Gemini v3.0 concern 6 |
| 3 | Three-track × three-category crosstab (9 cells) | v3.0 |
| 4 | Per-position annualized return, arithmetic mean, net of fees | v3.0; locked single method in v3.1, Gemini v3.0 concerns 7 & 8 |
| 5 | Cancel-and-replace: 24-hour timer, plus 2-hour out-of-zone timer | v3.0, A.1; out-of-zone rule added v3.2, Gemini v3.1 concern 3 |
| 6 | Hold-to-settlement only; no multi-entry/exit in v0.1 | v3.0 |
| 7 | Primary cell pre-registered: Track 2 × single-binary | v3.1, Gemini v3.0 concern 3 |
| 8 | Descriptive cells reported but not promotion-eligible | v3.1, Gemini v3.0 concern 3 |
| 9 | Sample-size floor of 250 applies to primary cell only | v3.1, Gemini v3.0 concern 4 |
| 10 | Verdict ladder with marginal-strong/medium/weak tiers | v3.0 |
| 11 | Strategy-abandonment vs thesis-abandonment distinction | v3.0 |
| 12 | Filled-positions-only for return metrics; attempted-posts for fill rate | v3.0 |
| 13 | Spread measured per-attempt, averaged across attempts | v3.0 |
| 14 | Position size fixed at $1,000 nominal (no discretionary range) | v3.1, Gemini v3.0 concern 2 |
| 15 | Re-entry permitted after stale-cancel and after filter-window exit | v3.2, Gemini v3.1 concerns 1 & 6 |
| 16 | Decay-check sample-size gate (N≥20 in 3 months, else expand or skip) | v3.2, Gemini v3.1 concern 4 |
| 17 | Voided/canceled markets included on best-effort basis with opportunity-cost return | v3.2, Gemini v3.1 concern 5 |
| 18 | Phase 1c restricted to forward data only; no same-data re-test | v3.2, Gemini v3.1 concern 2 |
| 19 | Track 1 thesis-level transparency check (reporting requirement, not pass/fail) | v3.2, Gemini v3.1 concern 7 |
| 20 | Thesis statement (section 1) framed as systematic mispricing, not as tautological "above 50¢ earns positive"; strategy described as bilateral (buy favorites + sell longshots) throughout | v3.2.1, author review |
| 21 | Capital-allocation tie-breaker: earliest open date, alphabetical ticker | v3.2.2, Gemini v3.2.1 concern 1 |
| 22 | Headline metric changed to median per-position annualized return; arithmetic mean reported as diagnostic | v3.2.2, Gemini v3.2.1 concern 2 |
| 23 | Statistical-significance test changed to Wilcoxon signed-rank (consistent with median-based metric) | v3.2.2, Gemini v3.2.1 concern 2 |
| 24 | Entry checks performed once per UTC day at 00:00 UTC against just-finalized daily candle | v3.2.2, Gemini v3.2.1 concerns 3 & 8 |
| 25 | Phase 1c statistical threshold tightened to p<0.00625 (Bonferroni / 8 descriptive cells) | v3.2.2, Gemini v3.2.1 concern 4 |
| 26 | Voided-market detection logic must be documented in `notes/voided-market-detection.md` and committed before Test B run; locked at that point | v3.2.2, Gemini v3.2.1 concern 5 |
| 27 | Utilization (capital-days deployed ÷ capital-days available) added as diagnostic, not as kill condition | v3.2.2, Gemini v3.2.1 concern 6 (modified) |
| 28 | Direction-within-zone rule: tighter spread first, larger OI second, sell-YES default | v3.2.2, Gemini v3.2.1 concern 7 |
| 29 | Empty-side spread treated as $1.00 for direction-within-zone comparison | v3.2.3, Gemini v3.2.2 concern 2 |
| 30 | Wilcoxon signed-rank uses Pratt's method for tie handling (preserves zero-alpha voided-market positions in significance test) | v3.2.3, Gemini v3.2.2 concern 3 |
| 31 | Mean-vs-median sanity check added as 8th PASS condition (mean ≥ median − 2 SE of median) | v3.2.3, Gemini v3.2.2 concern 4 |
| 32 | Walkthrough markets (Mojtaba, Costa Rica, Canadian Liberals) excluded from Test B sample | v3.2.3, Gemini v3.2.2 walkthrough-leakage concern |
| 33 | API retry / live-streaming implementation details deferred to Phase 1b notes document | v3.2.3, Gemini v3.2.2 concern 1 |

### v3.3 substantive reframing (driven by universe-sizing data, not review concerns)

| # | Decision | Source | Reasoning |
|---|---|---|---|
| 34 | Test B reframed from confirmatory falsifying test to hypothesis-formation exercise | v3.3, Path B universe-sizing data | Universe contains ~164 candidate markets, not the ~1,500+ assumed in v3.2.3. The 250-position primary-cell floor was unmeetable. Reframing aligns the test with what the data can actually support. |
| 35 | Multi-outcome 5+ bucket events excluded from universe entirely | v3.3, walkthrough A.2 + universe-sizing data | A.2 showed 5+ bucket events compress the bias signal across many dead-zone-priced buckets; universe-sizing showed they are 65% of long-dated in-universe events but contribute most of the noise. Trade universe size for signal clarity. |
| 36 | Pass conditions reduced from 8 to 4 | v3.3 | T-bill+3pp threshold, p<0.05 Wilcoxon, adverse-selection cap, spread cap, sample-size 250, decay sub-cut all moved to diagnostics or to Phase 1b. Retained: median ≥ T-bill, fill rate ≥ 30%, mean-vs-median sanity, N ≥ 30. Calibrated for hypothesis-formation, not confirmatory test. |
| 37 | Return threshold relaxed from T-bill+3pp to ≥ T-bill at v0.1 | v3.3 | At small N, T-bill+3pp would reject ~40% of real edges and accept ~15% of non-edges. ≥T-bill rejects ~5% of real edges and accepts ~50% of non-edges. The downstream Phase 1b paper-shadow stage catches the false positives at the +3pp bar where it belongs. |
| 38 | Sample-size floor reduced from 250 to 30 | v3.3 | 30 is the honest minimum below which results are descriptive only. Anything higher would routinely produce "insufficient sample, no verdict" given universe size. |
| 39 | Phase 1c forward-data follow-up testing apparatus removed entirely | v3.3 | Phase 1c (with its Bonferroni correction at p<0.00625) was the disciplined response to multiple-comparisons in a confirmatory test. Once Test B is hypothesis-formation, "look at descriptive cells, paper-shadow the most promising" is correct methodology, not data snooping. Phase 1b paper-shadow at T-bill+3pp serves the role Phase 1c was meant to serve. |
| 40 | DESCRIPTIVE PASS path added: any cell meeting 4 conditions can advance to Phase 1b paper-shadow on that cell's parameters | v3.3 | Replaces the "primary cell only triggers PASS" rule with a more permissive rule that respects the hypothesis-formation framing. v0.1 in its primary-cell-defined form is abandoned in this case, but a related cell becomes Phase 1b's subject. |
| 41 | Live advisory-shadow plan deferred to Phase 1b session | v3.3, author decision | The reviewer's parallel historical-and-live-shadow plan was correct in principle but adds scope to Session B beyond what is currently feasible. Deferred to the separate Phase 1b session, which will have its own pre-registration cycle. |

### Concerns from Gemini reviews not incorporated, with reasoning

| Concern | Source | Disposition | Reasoning |
|---|---|---|---|
| Add control track of short-dated markets | Gemini v3.0, concern 5 | Not adopted | Real external-validity risk acknowledged in section 5. A control track expands scope beyond v0.1. Test B's purpose is to detect the bias on this universe specifically; a null result is informative without requiring a control. |
| Cut Appendix A (walkthroughs) | Gemini v3.0, concern 9 | Not adopted | Walkthroughs are kept as the documented justification for several locked rules. Their presence makes the rule selection auditable rather than appearing arbitrary. |
| Track 1 ≥ T-bill as co-equal pass condition | Gemini v3.1, concern 7 | Adopted as transparency check, not as kill condition | Adding Track 1 as a co-equal kill condition reverts toward the multiple-comparisons problem the primary-cell design was meant to solve. The transparency check (section 6.7) preserves the honesty Gemini was asking for without creating a second pass gate. |
| Apply T-bill+3pp hurdle to total $30K committed pool, not deployed capital | Gemini v3.2.1, concern 6 | Not adopted; replaced with utilization diagnostic | Penalizing total committed capital re-introduces the dead-zone-as-cost framing that v3.1 explicitly rejected. Idle capital earns T-bill, so per-position is the honest comparison. The legitimate concern Gemini raises (low-utilization strategies passing the test but being economically irrelevant) is addressed by reporting utilization as a diagnostic; a low-utilization PASS is information about scope, not a hidden failure. |
| Delete Phase 1c "thesis success" path; primary cell must be sole gate | Gemini v3.2.1, concern 4 | Not adopted as full deletion; addressed via Bonferroni correction | Deleting Phase 1c discards real information when a descriptive cell shows promise on a different slice than the primary cell. The Bonferroni correction (decision #25) tightens the false-positive rate to a defensible level while preserving the path. |
| Annualization-skew fix via dollar-weighted return on $30K pool | Gemini v3.2.1, concern 2 | Not adopted as proposed; addressed via median metric | Dollar-weighted on the $30K pool is functionally the "total committed capital" hurdle Gemini also proposes in concern 6, with the same problem (penalizes idle time that is economically not idle). The median fix addresses the skew concern without changing the denominator philosophy. |
| Utilization floor as kill condition | Gemini v3.2.1 concern 6, **re-raised in Gemini v3.2.2 concern 5** | Re-confirmed not adopted | Utilization remains a diagnostic per author decision in v3.2.2. The "ghost strategy" risk Gemini describes (passing on technicalities while being economically irrelevant) is real but is information about scope to be acted on at the Phase 1b promotion decision, not a Phase 1a kill condition. Test B's job is to detect the bias; deployability is a separate question that gets answered at Phase 1b transition. |
| Run live shadow in parallel with historical Test B | Clean Claude review on v3.2.3 | Adopted in spirit; live shadow deferred to separate Phase 1b session | The reviewer's "live shadow first, historical second" is structurally correct but Session B's scope is currently historical-only by author decision. The disagreement-between-live-and-historical rule (do not deploy real money if they diverge) is preserved and will be specified in Phase 1b's pre-registration document. |
| Cut to a 4-page v0.1 spec | Clean Claude review on v3.2.3 | Adopted in spirit, not literally | v3.3 cuts pass conditions, removes Phase 1c, simplifies the verdict ladder, and tightens Appendix A. The result is shorter and lower-complexity than v3.2.3 but is not 4 pages. The full decisions log in Appendix B is preserved per author decision (logic and history matter for the next iteration). |

---

*This document is the binding pre-registration for Phase 1 of the Kalshi favorite-longshot trading project. Commit hash: [to be filled at commit].*
