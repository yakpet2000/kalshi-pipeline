# Stage 3b Pre-Registration

**Status:** pre-registration document for Stage 3b verdict computation.
**Locks at:** the commit that adds this file. After lock, no decision in this
document may be revised. Computation bugs in `scripts/compute_verdict.py` are
fixable; criteria refinements after seeing intermediate numbers are not.
**Author:** Peter Yakovlev.

---

## 1. Purpose and scope

Stage 3b applies the locked §6 verdict criteria from `investment-thesis.md`
(v3.3) to `notes/test-b-positions.csv` (Stage 2b output, locked at commit
13b048b) and emits a verdict to `notes/test-b-verdict.md`.

This document is divided in two halves:

- **Half A** restates the §6 verdict criteria so they are fixed in writing in
  one place before the positions CSV is opened.
- **Half B** locks the input decisions §6 underspecifies — the T-bill
  comparison, the standard-error-of-median estimator, the annualization
  convention, the fill-rate denominator, voided-market handling, and a few
  smaller items.

Half A is restatement, not revision. If anything in Half A diverges from
`investment-thesis.md` v3.3 §6, the thesis controls and this document is wrong.

Half B fixes underspecified inputs that the thesis leaves open. These
decisions are themselves locks: they cannot be revised after this document is
committed.

---

## Half A — Restatement of locked §6 criteria

### 2. Crosstab structure (§6.1, §6.2)

Three tracks × two structural categories = six cells, all pre-registered.

- Track 1 (full): all v0.1 positions, both halves combined.
- Track 2 (favorite-buy only): positions in the favorite zone (≥85¢).
- Track 3 (longshot-sell only): positions in the longshot zone (≤15¢).

- single-binary
- multi-outcome 2-4

Multi-outcome 5+ events are excluded from the universe entirely (§2 of thesis)
and do not appear as a category.

### 3. Primary cell (§6.3)

**Primary cell: Track 2 × single-binary.**

The strategy's PASS/FAIL verdict is determined by the primary cell only. The
five descriptive cells are reported alongside but do not promote to PASS.

### 4. Headline metric (§6.4)

**Median per-position annualized return, net of all Kalshi fees.**

The arithmetic mean is reported as a diagnostic alongside the median.

### 5. Pass conditions (§6.6)

The primary cell passes if and only if **all four** of the following hold
simultaneously:

| # | Condition | Threshold |
|---|---|---|
| 1 | Annualized return (median per-position, net of fees) | ≥ T-bill rate |
| 2 | Maker fill rate | ≥ 30% |
| 3 | Mean-vs-median sanity check | mean ≥ median − 2 × SE(median) |
| 4 | Sample-size floor | N ≥ 30 filled positions |

Specific operationalization of conditions 1 and 3 is locked in Half B.

### 6. Track 1 transparency check (§6.7)

If the primary cell PASSes but Track 1 (full strategy, all categories
combined) returns ≤ 0%, the verdict document prominently reports the §6.7
boilerplate text, distinguishing primary-cell pass from broader-thesis
support.

### 7. Filled-vs-attempted semantics (§6.8)

Return metrics operate on filled positions only. Fill rate operates on
attempted posts. Both are reported separately. A primary cell with strong
filled returns but fill rate <30% fails condition 2 and is treated as
untradeable.

### 8. Verdict ladder (§6.9)

- **PASS:** primary cell satisfies all 4 conditions. Phase 1b paper-shadow on
  primary-cell parameters.
- **DESCRIPTIVE PASS:** no primary-cell pass, but at least one descriptive
  cell meets all 4 conditions. Phase 1b paper-shadow runs on the
  closest-passing descriptive cell. v0.1 in primary-cell-defined form is
  abandoned.
- **INSUFFICIENT SAMPLE:** primary cell yields fewer than 30 filled positions.
  Descriptive results only; no PASS/FAIL.
- **FAIL:** no cell (primary or descriptive) meets all 4 conditions. v0.1
  abandoned. Thesis itself is reviewed if and only if Track 1 also returns
  ≤ 0%.

---

## Half B — Locked inputs §6 underspecifies

### 9. T-bill comparison

§6.6 condition 1 reads "annualized return ≥ T-bill rate." This leaves the
rate value, the tenor, the source, and the comparison shape underspecified.
Locks below.

**Source:** FRED series `DGS3MO` (3-Month Treasury Bill: Secondary Market
Rate), daily values.

**Tenor:** 3-month. Defensible alternative is 4-week (`DGS4WK`); 3-month is
selected as the academic-standard risk-free benchmark, with the difference
between tenors over the test window bounded at ~10–30 bps. The cash-sweep /
short-duration-T-bill-ETF comparison from §131 of the thesis is satisfied by
either tenor; 3-month is chosen for series quality and standardization.

**Per-position rate assignment:** each position is assigned the `DGS3MO` value
on its fill date. If the fill date is a non-business day or a date for which
`DGS3MO` is not reported (federal holidays), the most recent prior business
day's value is used.

**Excess return:** for each position `i`,
`excess_i = annualized_return_i − DGS3MO_i`.

**Binding pass criterion (Option 2):** the primary cell satisfies condition 1
if and only if `median(excess_i) ≥ 0` across the cell's positions.

**Sensitivity check (Option 1, reported but not gating):** also report
`median(annualized_return_i)` against `mean(DGS3MO over the test window)`. If
Option 1 and Option 2 disagree on pass/fail for the primary cell, the verdict
document flags the disagreement explicitly. The binding criterion remains
Option 2.

**Reasoning for Option 2 over Option 1.** Test-window 3-month T-bill rates
range from approximately 0.05% (2021) to approximately 5.4% (2024). A single
windowed-average rate over-penalizes positions that settled in the low-rate
era and under-penalizes positions that settled in the high-rate era. Per-
position excess matches the §6.4 framing that idle capital "earns the
benchmark rate" — the relevant opportunity cost is what T-bills paid while
each position was actually held. The literal "median ≥ T-bill" reading of
§6.6 is best understood as shorthand for the per-position framing made
explicit in §222 of the thesis, not as an instruction to use a single number.

This is a specification refinement, not a methodology revision: §6.6 was
underspecified on this point and Half B's job is to lock it before the data
is examined. Reporting Option 1 as a sensitivity check inoculates against the
revision-vs-refinement objection.

### 10. Standard error of median

§6.6 condition 3 uses "2 × standard error of median" but does not specify the
estimator.

**Estimator:** non-parametric bootstrap.

**Resamples:** 10,000.

**Seed:** `42` (pinned for reproducibility; rerun must produce identical SE).

**Distribution operated on:** the excess-return distribution from §9, not raw
annualized returns. The sanity check is computed in the same return space as
the headline pass criterion, for internal consistency.

**Reasoning.** Bootstrap is distribution-free, well-understood at N≈30–50,
and avoids the normality assumption built into the asymptotic
`1.2533·σ/√n` estimator. We have no reason to expect per-position annualized
returns to be normally distributed; they will be skewed by short-holding-
period annualization and possibly fat-tailed.

### 11. Annualization convention

§6.4 says "annualized using calendar days from fill to settlement" but does
not specify compounding shape or day-count basis.

**Compounding:** linear. `annualized_return_i = holding_period_return_i ×
(365 / holding_days_i)`.

**Day-count basis:** 365.

**Reasoning.** The §6.4 motivating example ("1% gain on a 2-day trade
annualizes to ~180%") is itself linear arithmetic: 1% × 365/2 = 182.5%, while
the geometric answer would be ~611%. The thesis pre-supposed linear
annualization in its example. Locking linear here is consistency with the
thesis, not a fresh choice. The 365-day basis matches `DGS3MO` quoting
convention, keeping the §9 comparison apples-to-apples.

### 12. Fill-rate denominator

§6.8 specifies that fill rate is `filled / attempted`, but does not specify
where attempted-post counts live in the Stage 2b output.

**Schema lookup procedure.** In Stage 3b sub-stage 3b.1, run `head -1` on
`notes/test-b-positions.csv` and, if present, `head -1` on any attempts log
co-located with it (e.g., `notes/test-b-attempts.csv` or whatever the
simulator emitted). Inspect column headers only; do not read row contents.
Document the schema finding inline in this section before this document is
committed.

**Schema finding:** `notes/test-b-positions.csv` is the sole positions /
attempts file in `notes/test-b-*`; there is no separate attempts log. The
simulator-design.md §4 schema is one-row-per-attempted-post, so attempted
counts and filled counts both live in the same CSV. Header inspection
(`head -1`) returns these 23 columns: `ticker, event_ticker, series_ticker,
primary_bucket, structure, post_date, side, limit_price, contracts_attempted,
capital_deployed, outcome, fill_date, fill_price, total_fees, settlement_date,
settlement_outcome, settlement_value_per_contract, position_pnl,
position_pnl_net_fees, position_return, holding_period_days,
annualized_return, tbill_rate_at_fill`. The `outcome` column distinguishes
attempt outcomes — `filled`, `stale_cancelled`, `out_of_zone_cancelled`,
`blocked_by_cap`, or `blocked_by_filter`. Per `simulator-design.md` §4
(the locked output schema), the `settlement_outcome` column is populated
only for `outcome == 'filled'` rows and takes values `yes`, `no`, or
`voided`; it is empty for non-filled attempts.

**Locked fill-rate computation:** within a given cell (track × structure
slice), let `attempted = count(rows in cell)` and
`filled = count(rows in cell where outcome == 'filled')`. The cell's fill
rate is `filled / attempted`. Voided positions are included in `filled` by
construction: per the simulator's locked design, a voided market still
produces an `outcome = 'filled'` row (with `settlement_outcome = 'voided'`),
so the simple `outcome == 'filled'` count captures them. This matches the
boundary-case rule below without requiring a separate clause in the formula.

**Boundary case — voided positions.** Voided positions count as filled for
fill-rate purposes. Voiding occurs after fill; the strategy successfully got
a position on, and treating voiding as not-filled would penalize the fill-
rate metric for events outside the strategy's control.

### 13. Voided-market handling

§82 of the thesis specifies that voided-market positions are assigned a
return equal to the T-bill rate over the lockup period, "functionally zero
alpha." This is operationalized consistently with §9 above.

**Voided-position annualized return:** `annualized_return_i =
DGS3MO_at_fill_date_i`. Equivalently, `excess_return_i = 0` by construction.

**Inclusion in metrics:**
- Counted as filled for fill-rate (per §12 above).
- Counted toward N for the sample-size floor (§6.6 condition 4).
- Contribute zero excess return to the cell median.
- Contribute their per-position raw annualized return (= `DGS3MO_at_fill`) to
  Option 1 sensitivity check.

**Diagnostic to report.** The verdict document reports the count and fraction
of voided positions per cell. If a high voided fraction (e.g., >20%) is
present in any cell, this is flagged as context for interpreting that cell's
median, since voided positions pull the median toward zero by construction.

### 14. Median tie-handling

For even N: standard average-of-two-middles. `median = (x[N/2] + x[N/2+1]) /
2` after sorting, 1-indexed. This is the numpy / pandas / scipy default.

### 15. Bootstrap seed

`42`. Pinned for reproducibility of the §10 sanity check. Pinning the seed is
not a methodology decision; it ensures that re-running the verdict
computation produces identical numbers, which matters because condition 3 of
§5 is one of the four pass conditions.

---

## 16. What is not locked here

The following are deliberately not locked in this document:

- **The verdict itself.** That is computed in Stage 3b sub-stage 3b.2 and
  reported in 3b.3.
- **The §6.5 diagnostic metrics** (Sharpe, max drawdown, adverse-selection,
  spread, utilization, p-value, mean as skew indicator, sample size). These
  are reporting requirements, not pass conditions; their computation follows
  standard conventions and does not require pre-registration discipline. Any
  ambiguity discovered during 3b.2 is documented in the computation script,
  not promoted to a methodology lock here.
- **Phase 1b decisions.** Per §6.10 and §6.9, Phase 1b is specified in a
  separate pre-registration document at the time it runs.

---

## 17. Pre-registration commitments specific to Stage 3b

Beyond the §6.10 commitments inherited from the thesis, I commit, before
Stage 3b sub-stage 3b.2 begins:

- Not to revise any decision in §§9–15 of this document.
- Not to swap Option 2 and Option 1 in §9 after seeing intermediate
  computation output. Option 2 is binding regardless of which one passes.
- Not to revise the bootstrap seed in §10/§15 to obtain a different SE value.
- Not to redefine "filled" or "attempted" in §12 after the schema finding is
  recorded in this document and committed.
- Not to exclude voided positions from N or from fill-rate after seeing what
  fraction of the data they constitute.

If a result requires a parameter change in this document to look good, the
result is the answer. The change is not.

---

*Lock commit: [to be filled at commit].*
