# Test B universe construction — v0.1 (locked)

**Status:** pre-registration document for Session B Stage 1b. Locked
before the Test B simulator runs on settled-market data. Cannot be
revised after Stage 2 simulator code begins running. Same discipline as
`notes/investment-thesis.md` and the Stage 0 lock documents.

This document satisfies the universe-construction step required between
the thesis (which defines the universe abstractly) and the simulator
(which consumes the universe table). The output is `notes/test-b-universe.csv`.

**Commit hash:** [to be filled at commit].

---

## 1. Inputs

The universe is constructed from three input artifacts and one external
API surface:

- **`notes/candidate-universe.csv`** — Session 3a output (commit
  `69ed40c`), 6,550 rows. Provides per-market `ticker`, `event_ticker`,
  `series_ticker`, `status`, `open_time`, `close_time`, `settle_time`,
  and other fields not used by Stage 1b.
- **`notes/series-bucket-assignments.csv`** — input artifact (already on
  disk before Stage 1b began), 111 series. Provides per-series
  `primary_bucket`, `walkthrough_excluded`, `out_of_scope_excluded`,
  `notes`. Treated as immutable input by Stage 1b. See §4 for the
  classification methodology.
- **`notes/candle-data-probe.md`** — Stage 1a feasibility note, in
  particular §1 (endpoint and parameters), §6 gap #2 (~12.5% unreachable
  rate), §6 gap #5 (universe lockdate at 2026-05-04).
- **The Kalshi candlesticks endpoint** — `GET /series/{series_ticker}/markets/{ticker}/candlesticks`,
  unauthenticated, used at the reachability step only.

No other inputs. In particular, no DB access (`DATABASE_URL` is not
read), no `/markets/{ticker}` calls (per Stage 1a §6 gap #2 the
candlesticks endpoint is the binding reachability constraint), and no
LLM/agent calls of any kind.

---

## 2. Filter pipeline (ordered)

Stage 1b applies the following filters **in order**. Order is
pre-registered: changing it could change the surviving set when
diagnostics are reported per-stage. The order also reflects API-cost
discipline — cheap, deterministic filters run before the expensive
reachability probe.

### Filter steps

1. **Status filter.** Keep only rows with `status == "finalized"` from
   `candidate-universe.csv`. (1,256 of 6,550 rows survive at this
   stage given the current input.)

2. **Series-bucket join.** Inner-join the surviving rows to
   `series-bucket-assignments.csv` on `series_ticker`. Markets in
   series not present in the bucket assignment file are dropped. This
   is the universe-membership gate: only markets in classified series
   are eligible for Test B.

3. **Walkthrough exclusion.** Drop rows whose joined
   `walkthrough_excluded == "true"`. Per thesis §2 walkthrough-
   exclusion clause, the three series flagged as walkthrough-derived
   (KXNEXTIRANLEADER, KXMOVCOSTARICAPRESR1, KXCANADALIBERAL — see
   thesis Appendix A) are excluded regardless of structure or bucket.

4. **Out-of-scope exclusion.** Drop rows whose joined
   `out_of_scope_excluded == "true"`. The single series flagged
   (KXBEZELD, a luxury-product market) is removed.

   **Defensive assertion (not a filter step).** After step 4, every
   surviving row has a non-empty `primary_bucket`. The construction
   script asserts this invariant. If it is violated, the script fails
   loudly rather than silently dropping rows; this is intentional —
   silent drops here would mask a bucket-assignment-CSV bug.

5. **Lifespan filter.** Compute
   `lifespan_days = floor((expected_settlement_time − open_time) / 1 day)`
   in calendar days, where `expected_settlement_time = settle_time if
   non-empty else close_time`. Drop rows with `lifespan_days < 30`.
   Rows with missing `open_time`, or with both `settle_time` and
   `close_time` empty, are dropped at this step (recorded as a separate
   diagnostic count).

6. **Multi-outcome cardinality filter.** Group survivors by
   `event_ticker`. Compute cardinality per event. Drop events with
   `cardinality ≥ 5` (per thesis §2 universe-definition: 5+ bucket
   events excluded). Tag the rest:
   - `structure = "single-binary"` if cardinality == 1
   - `structure = "multi-outcome-2-4"` if 2 ≤ cardinality ≤ 4

7. **Reachability probe.** For each surviving market, call the Kalshi
   candlesticks endpoint once: `GET /series/{series_ticker}/markets/{ticker}/candlesticks`
   with `start_ts = floor(open_time − 1 day)`, `end_ts =
   ceil(expected_settlement_time + 1 day)`, `period_interval = 1440`.
   Reachability is defined as **HTTP 200 with non-empty `candlesticks`
   array**. Drop markets that return HTTP 404 or HTTP 200 with empty
   array. On HTTP 429 the probe retries with exponential backoff
   (5s / 15s / 45s, max 3 retries); after exhausting retries the
   market is treated as unreachable. On any other non-200 response the
   market is treated as unreachable and the response is logged. For
   survivors, record `candle_count = len(candlesticks)`.

### Effective window population

After step 7, the surviving rows are annotated with two output columns:
- `effective_window_start = open_time`
- `effective_window_end = expected_settlement_time`

For v0.1 these equal the contract's full lifespan. The thesis §2
scheduled-binary-event filter is implemented as a filter machine with
an **empty event schedule** (see §7 limitation #2 below). The columns
exist as the schema-correct location to hold the post-filter window
when (and if) a curated schedule is introduced in Phase 1b.

---

## 3. Bucket assignment methodology

Series-level hand-classification was the procedure. Each of the 111
classified series was mapped to exactly one of the five thesis-§2
primary buckets (or flagged for exclusion). The mapping lives in
`notes/series-bucket-assignments.csv`.

The classifier was the user, not Claude. Claude proposed candidate
mappings in chat for each series; the user reviewed and made the final
call per series. The CSV is the authoritative output of that process
and is treated as an **input artifact** by Stage 1b. It is not edited
by the construction script.

Why series-level (rather than per-market) classification: a series
corresponds to a coherent topic — `KXFEDDECISION` is FOMC meeting
outcomes, `KXNFP` is non-farm payrolls, `KXCANADALIBERAL` is the
Canadian Liberal majority question. All markets within a series share
the same thesis-bucket assignment by construction. Per-market
classification would multiply work by ~10× without changing
assignments.

---

## 4. Output schema — `notes/test-b-universe.csv`

The construction script writes a single CSV with the following columns,
in this order:

| Column | Type | Meaning |
|---|---|---|
| `ticker` | string | Kalshi market ticker, primary key |
| `event_ticker` | string | Kalshi event ticker, used for grouping and structure cardinality |
| `series_ticker` | string | Kalshi series ticker |
| `primary_bucket` | string | One of {`macro`, `geopolitics`, `us_politics`, `us_political_appointment`, `policy_outcome_quantitative`} |
| `structure` | string | One of {`single-binary`, `multi-outcome-2-4`} |
| `open_time` | string | UTC ISO 8601, from `candidate-universe.csv` |
| `expected_settlement_time` | string | UTC ISO 8601, = `settle_time` if non-empty else `close_time` |
| `effective_window_start` | string | UTC ISO 8601, = `open_time` for v0.1 (empty-schedule filter) |
| `effective_window_end` | string | UTC ISO 8601, = `expected_settlement_time` for v0.1 |
| `lifespan_days` | integer | `(expected_settlement_time − open_time)` in calendar days; ≥30 by construction |
| `candle_count` | integer | Number of daily candles returned by the reachability probe |
| `reachable` | string | Always `"true"` — unreachable markets are not in the file by construction |

Encoding: UTF-8, LF line endings. Sort order: `ticker` ascending. The
sort is explicit (not relying on dict iteration order) so re-running
the script with identical inputs produces a byte-identical CSV.

---

## 5. Pre-registered edge-case decisions

- **Unreachable markets** are filtered out at universe-construction time
  per `notes/candle-data-probe.md` §6 gap #2. The count is reported as
  a diagnostic in the funnel (§6 below). Unreachability is **not** a
  Test B kill condition; the universe simply excludes them.

- **Walkthrough markets** (3 series; thesis Appendix A) are dropped
  regardless of structure, bucket, or lifespan. This is binding from
  the thesis lock, not a Stage 1b decision.

- **Out-of-scope markets** (1 series, KXBEZELD) are dropped.

- **Multi-outcome cardinality.** Events with ≥5 markets are dropped
  entirely (per thesis §2). Events with 1 market are tagged
  `single-binary`; events with 2–4 markets are tagged
  `multi-outcome-2-4`. The cardinality is computed against the
  candidate-universe.csv input — it counts every market in the event
  that reached step 6, regardless of whether each individual market
  itself survives later filters.

- **Lifespan boundary** (`lifespan_days < 30`) drops markets with
  shorter lifespans. Markets missing `open_time` or both
  `close_time` and `settle_time` are also dropped at this step.

- **Settled-only.** Only `status=finalized` rows are eligible. No
  partial-settlement, no in-progress, no expired-but-not-finalized
  cases are considered. Per thesis §2 the universe is settled history
  only.

- **Effective trading window** for v0.1 equals the full contract
  lifespan (see §2 effective-window subsection and §7 limitation #2).

---

## 6. Diagnostic counts (funnel format)

The construction script prints the following funnel to stdout on every
run, in this exact format. The funnel is the auditable record of which
filter step removed which markets:

```
candidate-universe.csv rows                     =    N
  after status=finalized                        =    N
  after series in bucket-assignments            =    N
  after walkthrough_excluded=false              =    N
  after out_of_scope_excluded=false             =    N
  (assertion: primary_bucket non-empty for all)
  after lifespan >= 30 days                     =    N
  after multi-outcome cardinality (drop 5+)     =    N
  after reachability probe (drop 404/empty)     =    N
final test-b-universe.csv rows                  =    N

structure breakdown (final):
  single-binary                                 =    N
  multi-outcome-2-4                             =    N

bucket breakdown (final):
  macro                                         =    N
  geopolitics                                   =    N
  us_politics                                   =    N
  us_political_appointment                      =    N
  policy_outcome_quantitative                   =    N

reachability diagnostic:
  attempted probes                              =    N
  reachable (HTTP 200 with non-empty candles)   =    N
  unreachable (HTTP 404)                        =    N
  unreachable (HTTP 200 empty array)            =    N
  unreachable (other / 429-after-retries)       =    N
```

The run output of this funnel is shown to the user before any commit;
unexpected counts trigger a stop-and-discuss per the Stage 1b protocol.

**First-run observation (2026-05-06):** the actual reachability rate
was 98.0% (98 of 100), substantially higher than Stage 1a's 12.5%
prior. The Stage 1a probe was based on 8 markets including
`KXTRUMPMEETING-27JAN01-NZMAM`, which itself was filtered out of the
universe by the multi-outcome cardinality filter before reachability
was tested. The 12.5% prior was therefore based on a sample whose
composition differed from the universe's actual composition. We accept
the actual 2.0% unreachable rate as the operative number for the
locked v0.1 universe.

---

## 7. Documented limitations

1. **Correlated markets within events are treated as independent
   positions.** A multi-outcome-2-4 event with 3 buckets contributes 3
   independent rows to the universe even though the 3 buckets'
   outcomes are mutually constrained (exactly one settles YES). Test
   B's Wilcoxon signed-rank test treats each filled position as
   independent, which understates the test's effective sample size
   when correlated markets are filled. This is a known limitation, not
   a defect; tightening would require a hierarchical statistical
   model that is out of scope for v0.1.

2. **The thesis §2 scheduled-binary-event filter is implemented with
   an empty event schedule.** The filter machinery exists (the
   `effective_window_start` / `effective_window_end` columns are
   populated with the schema-correct values), but no events are
   excluded because no schedule has been curated. v0.1 is therefore
   over-permissive on shock-windows around scheduled elections, FOMC
   meetings, BLS releases, and the other event types listed in thesis
   §2. This biases v0.1 toward over-stating fills that occurred near
   scheduled events. Phase 1b paper-shadow (per thesis §7) is the
   correct stage at which to introduce a curated event schedule.

3. **Universe lockdate is 2026-05-04**, the discovery-run completion
   date recorded in `notes/universe-discovery.md`, per
   `notes/candle-data-probe.md` §6 gap #5. Markets that settled
   between 2026-05-04 and Stage 2's run date are not in the universe.
   This absence is mechanical, not selection bias — fresh API
   enumeration cannot rebuild the older-than-24-hours settled set
   (Stage 1a §6 gap #5).

4. **The N≥30 sample-size floor (thesis §6.6 condition 4) is not
   guaranteed by universe construction.** Whether enough positions are
   filled to clear the primary cell's N≥30 floor depends on the
   strategy's posting and fill rules at Stage 2. The current pre-flight
   structure-cardinality count shows ~19 single-binary markets in the
   eligible set; after the favorite-zone subset and reachability the
   primary cell may verdict "INSUFFICIENT SAMPLE" per the thesis §6.9
   verdict ladder. This is anticipated, and an honest INSUFFICIENT
   verdict on the primary cell with descriptive results on other cells
   is a legitimate Test B output.

5. **Series-level rather than per-market classification.** Per §3, all
   markets in a series share the same primary bucket. If Kalshi were
   to introduce a series whose individual markets span multiple thesis
   buckets, the current methodology would over-aggregate. No such
   series appears in the 111-series classification used here.

6. **Microsecond-precision settlement timestamps in
   `expected_settlement_time` and `effective_window_end`.** Source
   data from `notes/candidate-universe.csv` carries timestamps with
   microsecond resolution (e.g., `2026-03-31T21:17:17.969110Z`). This
   precision is preserved in `notes/test-b-universe.csv` for fidelity.
   It does not affect Stage 2 logic, which operates on calendar dates
   per the Stage 1a date-labeling convention
   (`notes/candle-data-probe.md` §5). Flagged for transparency only.

---

## 8. Lock statement

This methodology cannot be revised after Test B's simulator code begins
running on the universe table. The same pre-registration discipline
applies as to the investment thesis and the Stage 0 lock documents:
revisions made *before* the simulator runs are legitimate; revisions
made *after* are p-hacking and disqualify the test. If a result requires
a methodology change to look good, the result is the answer — the
change is not.

The construction script (`scripts/build_test_b_universe.py`) is the
mechanical implementation of this methodology. It is committed
alongside this document. Re-running the script on the same inputs and
the same lockdate produces a byte-identical `notes/test-b-universe.csv`.
