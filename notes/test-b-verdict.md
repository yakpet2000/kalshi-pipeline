# Test B Verdict

**Verdict: INSUFFICIENT SAMPLE**
**Status:** lock document for Stage 3b verdict.
**Computed at:** commit 7ddb2b9 (`notes/test-b-verdict-numbers.json`).
**Author:** Peter Yakovlev.

---

## 1. Verdict statement

INSUFFICIENT SAMPLE. The primary cell (Track 2 favorite-buy × single-binary)
yielded `n_filled = 3` of `n_attempted = 320`, well below the 30-position
floor in `stage-3b-preregistration.md` §6.6 condition 4.

Per the verdict-ladder ordering locked in §6.9 plus the 3b.2 disambiguation,
INSUFFICIENT SAMPLE dominates regardless of descriptive-cell findings.
Phase 1b paper-shadow may still be considered on the cell with strongest
signal (acknowledged below the formal pass bar per §6.9), but no PASS or
DESCRIPTIVE PASS verdict is granted by this document.

---

## 2. Primary cell condition-by-condition

Track 2 (favorite-buy) × single-binary. `n_attempted = 320`, `n_filled = 3`.

| # | Condition | Threshold | Observed | Pass |
|---|---|---|---|---|
| 1 | Median excess return ≥ 0 (Option 2) | ≥ 0 | 1.169754 | True |
| 2 | Maker fill rate ≥ 30% | ≥ 0.3000 | 0.0094 (0.94%) | False |
| 3 | Mean ≥ median − 2 × SE(median) | ≥ 0.547774 | 1.168069 | True |
| 4 | N filled ≥ 30 | ≥ 30 | 3 | False |

- Condition 1: median per-position excess return is +1.169754 (linear-
  annualized, fraction of deployed capital), strictly above zero.
- Condition 2: the cell filled 3 of 320 attempted posts; the resulting fill
  rate of 0.94% is two orders of magnitude below the 30% threshold.
- Condition 3: the arithmetic mean (1.168069) lies within 2 SE of the
  median (SE = 0.310990); the mean-vs-median sanity check holds.
- Condition 4: with `n_filled = 3`, the cell is below the §6.6 sample-size
  floor of 30 by a factor of ten.

Conditions 2 and 4 fail; the primary cell does not satisfy all four. Per
§6.9, the failure of condition 4 in particular triggers the INSUFFICIENT
SAMPLE branch of the verdict ladder.

---

## 3. Descriptive cells

Five descriptive cells, reported per §6.3 alongside the primary cell. None
promote to PASS regardless of condition status (§6.3, §6.9).

| Cell | n_attempted | n_filled | fill_rate | median_excess | mean_excess | voided | C1 | C2 | C3 | C4 | all_4_pass |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Track 2 × multi-outcome 2-4 | 904 | 29 | 3.21% | 1.832265 | 4.592016 | 0 | T | F | T | F | False |
| Track 3 × single-binary | 920 | 29 | 3.15% | 14.909496 | 34.317913 | 0 | T | F | T | F | False |
| Track 3 × multi-outcome 2-4 | 2455 | 41 | 1.67% | 14.907673 | 20.377388 | 0 | T | F | T | T | False |
| Track 1 × single-binary | 1240 | 32 | 2.58% | 12.126376 | 31.210115 | 0 | T | F | T | T | False |
| Track 1 × multi-outcome 2-4 | 3359 | 70 | 2.08% | 3.462485 | 13.837734 | 0 | T | F | T | T | False |

All cells fail condition 2 (`fill_rate ≥ 30%`); observed fill rates span
0.94%–3.21% across the full crosstab.

Three cells meet the N ≥ 30 sample-size floor on their own — Track 3 ×
multi-outcome 2-4 (N = 41), Track 1 × single-binary (N = 32), and Track 1
× multi-outcome 2-4 (N = 70) — but each fails condition 2 on fill rate.

The `median_excess` values for the Track 3 cells (~14.9) and the Track 1
cells (~12.1 and ~3.5) are linear-annualized per `stage-3b-preregistration.md`
§11; the large magnitudes are the regime §11 acknowledged would arise from
short-holding-period sell-YES winners under linear `× (365 / holding_days)`
annualization.

---

## 4. T-bill window and Option 1 sensitivity

The fill-date range across the cell positions spans `2024-11-01` to
`2026-04-30`. The window-average `DGS3MO` over that span is 4.1237%
(`tbill_window_avg = 0.041237`). All six cells satisfy the Option 1
sensitivity check (`median_raw ≥ window_avg`); Option 1 and Option 2 do
not disagree on any cell, and the §9 disagreement flag does not fire.

Option 2 (per-position excess against the position's own fill-date
`DGS3MO`) is the binding criterion per `stage-3b-preregistration.md` §9.

---

## 5. Track 1 transparency check (§6.7)

Track 1 combined (all 102 filled positions across both structural categories):

- `track1_combined_n_filled`: 102
- `track1_combined_median_excess`: 5.575456
- `track1_combined_mean_excess`: 19.287893

The §6.7 transparency flag is **False**. The flag is defined in §6.7 to
fire only when the primary cell PASSes AND Track 1 median ≤ 0; the primary
cell did not PASS, so the conjunction is unsatisfied and the §6.7
boilerplate warning text does not apply.

The Track 1 combined `median_excess` is strongly positive at +5.575456.
This is information about the broader v0.1 strategy on this universe but
is not a pass condition under v3.3 and does not promote to a verdict.

---

## 6. Voided positions

Across all six cells: `voided_count = 0`, `voided_fraction = 0.00%`. The
§13 voided-market opportunity-cost machinery did not exercise on this run.
The §13 diagnostic-flag threshold (>20% voided fraction in any cell) was
not approached.

---

## 7. What this verdict implies

`stage-3b-preregistration.md` §6.9 INSUFFICIENT SAMPLE clause, verbatim:

> "Test B reports descriptive results only; no PASS/FAIL verdict...
> v0.1 strategy may still be paper-shadowed on the cell with most signal,
> but this is acknowledged as below the formal pass bar."

This document does not decide what comes next; that is a Phase 1b
pre-registration question. It does name the candidate cells whose
findings would inform such a decision:

- **Track 3 × multi-outcome 2-4**: largest filled sample (N = 41) of any
  descriptive cell, and the strongest `median_excess` (14.907673) of any
  descriptive cell.
- **Track 1 × multi-outcome 2-4**: largest filled sample overall (N = 70),
  with `median_excess = 3.462485`.

The fill-rate ceiling observed in this run (0.94%–3.21% across all six
cells) is itself informative. Per `maker-fill-model.md`, no quantitative
fill-rate prediction was locked; calibration of fill-rate magnitudes is
deferred to Phase 1b live-shadow data, where the rule will be revisited
(§3, §4 of `maker-fill-model.md`). The low fill rates may indicate that
maker fills at daily resolution are systematically rare on long-dated
Kalshi markets in the favorite-buy and longshot-sell zones, or may
indicate a permissive-rule miscalibration that live-shadow data will
reveal — both are open questions, not resolved by Test B.

---

## 8. What this verdict does NOT imply

- **Not a thesis rejection.** §6.9 specifies thesis review only when
  Track 1 median ≤ 0. Track 1 combined `median_excess` is +5.575456;
  the FAIL-with-thesis-review branch is not engaged.
- **Not a strategy abandonment beyond v0.1's primary-cell-defined form.**
  §6.9 INSUFFICIENT SAMPLE preserves the option to paper-shadow
  descriptive cells on author judgment. DESCRIPTIVE PASS-style abandonment
  of the primary-cell-defined form does not apply when the primary cell
  failed condition 4 rather than conditions 1–3.
- **Not a confirmation that the BDW favorite-longshot bias is capturable
  on Kalshi.** The sample is too small to support any confirmatory claim,
  which is exactly why the v3.3 reframing of Test B as hypothesis-formation
  (§6, opening paragraph) was correct.

---

## 9. Source traceability

- pre-registration: `notes/stage-3b-preregistration.md` @ 488fbb4
- positions CSV: `notes/test-b-positions.csv` @ 13b048b
- DGS3MO data: `reference-data/dgs3mo.csv` @ 1205656
- verdict computation script: `scripts/compute_verdict.py` @ c24bebf
- verdict numbers: `notes/test-b-verdict-numbers.json` @ 7ddb2b9
- this document: lock at the commit adding it.
