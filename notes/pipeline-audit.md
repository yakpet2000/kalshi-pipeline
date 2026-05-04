# Pipeline data audit

Read-only inspection of the `kalshi-pipeline` Postgres database to determine
whether the existing snapshot history can support a 90-day-pre / 90-day-post
backtest (Test B), and to surface any pipeline-level concerns along the way.

Database: local `kalshi_pipeline` on `postgresql://peteryakovlev@localhost:5432`,
loaded ~1 hour before this audit from a `pg_dump` of the Hetzner production DB.

Schema note: the snapshot timestamp column is `observed_at` (15-min UTC bucket),
not `snapshot_ts`. Source of truth: [`kalshi_pipeline/db.py`](../kalshi_pipeline/db.py)
and [`sql/schema.sql`](../sql/schema.sql).

---

## Q1. Snapshot history depth

**Query:**

```sql
SELECT
    MIN(observed_at) AS earliest_observed_at,
    MAX(observed_at) AS latest_observed_at,
    MAX(observed_at) - MIN(observed_at) AS wall_clock_span,
    COUNT(DISTINCT DATE(observed_at AT TIME ZONE 'UTC')) AS distinct_utc_days,
    COUNT(*) AS total_rows,
    COUNT(DISTINCT ticker) AS distinct_tickers
FROM market_snapshots;
```

**Raw result** (psql output, timestamps shown in session TZ `-07` / Pacific):

```
  earliest_observed_at  |   latest_observed_at   | wall_clock_span | distinct_utc_days | total_rows | distinct_tickers
------------------------+------------------------+-----------------+-------------------+------------+------------------
 2026-05-01 11:45:00-07 | 2026-05-04 05:45:00-07 | 2 days 18:00:00 |                 4 |       2385 |                9
```

**Interpretation:** Snapshot history covers a wall-clock span of **2 days
18 hours (~2.75 days)** across **4 distinct UTC calendar days** (the span
straddles 4 UTC date boundaries even though it is under 3×24h), with 2,385 rows
across 9 distinct tickers — consistent with ~265 snapshots/ticker, slightly
above the ~264 expected from 2.75 days × 96 polls/day (one extra bucket from
the half-hour trailing fragment).

The 4-day count vs. 2.75-day span is **not a divergence**: counting
distinct calendar days will always over-count a sub-N-day span that crosses
midnight, and 4 distinct UTC dates from a 66-hour window starting 18:45 UTC
is exactly what's expected. No flag.

---

## Q2. Survivorship

The data window is ~2.75 days and zero markets in the tracked set have resolved
in that window, so a snapshot-data query for survivorship would return a
trivially uninformative "100% retention." Q2 is therefore answered structurally
from the collector source code, not from the snapshot data. A single
confirmatory query is run at the end to rule out a "soft prune via YAML edit."

Two distinct flavors of survivorship are separated below.

### (a) Pruning bias — does the pipeline drop post-resolution rows?

**No. The pipeline is structurally immune to pruning bias.**

Tracing the write path:

1. The polling universe comes entirely from `tracked_markets.yml`
   ([collector.py:37](../kalshi_pipeline/collector.py#L37)). There is no
   status- or close-time filter applied to that list before fetching.
2. Each tick calls `client.get_market(ticker)`
   ([collector.py:43-46](../kalshi_pipeline/collector.py#L43-L46)), which hits
   the per-ticker detail endpoint `/markets/{ticker}`
   ([kalshi_client.py:103-112](../kalshi_pipeline/kalshi_client.py#L103-L112)).
   This endpoint keeps returning settled/resolved markets, so the API itself
   does not drop them from us.
3. Every successful fetch becomes a `SnapshotRow` unconditionally
   ([collector.py:65-85](../kalshi_pipeline/collector.py#L65-L85)) — no
   filter on `market.status`, no skip on `close_time` past, no skip on
   `last_price_dollars is None`.
4. The DB layer is a plain
   `INSERT … ON CONFLICT (ticker, observed_at) DO NOTHING`
   ([db.py:118-134](../kalshi_pipeline/db.py#L118-L134)) — no filter logic
   here either.
5. If a delisted ticker ever 404s, the `httpx.HTTPStatusError` branch
   ([collector.py:47-57](../kalshi_pipeline/collector.py#L47-L57)) logs,
   increments a `failed` counter, and continues — it does **not** mutate
   `tracked_markets.yml` or otherwise auto-prune the ticker. The row simply
   stops being produced; existing historical rows in `market_snapshots` are
   untouched.

**Implication for Test B:** post-resolution rows survive, and the row history
of any market that ever entered the tracked set is preserved indefinitely
(modulo Postgres-level retention, which is currently "forever" — there is no
DELETE/TRUNCATE/retention job in this codebase).

**Confirmatory query** (set-difference of snapshot tickers vs. current
YAML universe — flags any ticker that was historically polled but has since
been removed from `tracked_markets.yml`, i.e. a "soft prune via YAML edit"):

```sql
SELECT DISTINCT ms.ticker
FROM market_snapshots ms
WHERE ms.ticker NOT IN (
  'KXHORMUZWEEKLY-26MAY03-T50',
  'KXECONSTATCORECPIYOY-26JUN-T2.8',
  'KXMARENTCONTROL-26',
  'KXFOMCDISSENTCOUNT-26JUN-0',
  'KXKASHOUT-26APR-AUG01',
  'KXAAAGASED-26NOV03-4.50',
  'KXSPACEXCOUNT-26MAY-13',
  'KXCRITICALITY-26AUG-ATOMIC',
  'KXCBDECISIONEU-26JUN11-H25'
);
```

**Result:** 0 rows. Every ticker in `market_snapshots` is also in the current
YAML. Consistent with the code-reading: no soft prune, no edit-driven
universe shrinkage.

### (b) Selection bias — is the tracked universe representative?

**No. The pipeline is fully exposed to selection bias.**

`tracked_markets.yml` is a static, hand-curated YAML file at the repo root
([config.py:17-41](../kalshi_pipeline/config.py#L17-L41) — read-only loader,
no codepath in the package writes to it). The current 9 entries each carry a
`note` field documenting why they were picked (Strait of Hormuz traffic, Core
CPI YoY, MA rent control, FOMC dissent count, etc. — see
[tracked_markets.yml](../tracked_markets.yml)).

Two separate distortions follow:

1. The universe is whatever was hand-picked at file-edit time, not "all
   markets eligible at time T." 9 markets is not a representative sample of
   the Kalshi universe at any historical T.
2. The YAML can be edited at any time without leaving a trace inside Postgres.
   Historical re-creation of "what was the polling universe at time T" depends
   on `git log tracked_markets.yml`, not on data in the database. (The
   confirmatory query above can detect *removals* — historical tickers absent
   from the current YAML — but not *additions* of past tickers, since
   pre-addition periods leave no rows.)

**Implication for Test B:** any findings will be conditional on the
hand-picked universe, not generalizable to "long-dated low-volatility
prediction markets" as a class. This is a headline-level concern, not a Q2
footnote, and is promoted to "Headline findings" below.

### Sub-finding: empty `market_metadata` is explained by missing code, not a bug

`db.upsert_metadata` is defined
([db.py:137-152](../kalshi_pipeline/db.py#L137-L152)) but **never called**
anywhere in the package. `run_once` only calls `db.insert_snapshots`
([collector.py:89](../kalshi_pipeline/collector.py#L89)), and
[`__main__.py`](../kalshi_pipeline/__main__.py) exposes only the `collect`
subcommand — there is no daily metadata-refresh subcommand or scheduled
caller. The 0 rows in production are not a load failure; the refresh job
simply hasn't been built. Consistent with the project status in
[CLAUDE.md](../CLAUDE.md) (sessions 1/2/2.5 done; nothing claims a metadata
refresh shipped). Future work, not a blocker for Test B.

## Q3. Snapshot density and gaps

Density and gap structure are computed via
`LAG() OVER (PARTITION BY ticker ORDER BY observed_at)` to derive the interval
between each snapshot and the prior one for the same ticker. Three breakouts:
per-ticker median/max, per-ticker gap distribution by threshold, and
per-UTC-date gap distribution by threshold.

**Denominator note.** 2,385 rows across 9 tickers ⇒ 265 rows per ticker
⇒ **264 consecutive-pair intervals per ticker** (the first row of each ticker
has no LAG and is excluded). Per-ticker proportions below use 264 as the base.

**Up-front caveat.** With only ~2.75 days of data, gap-distribution claims are
weak. A single 1-hour outage on a single date would dominate the picture, and
their *absence* over 66 hours is consistent with either a genuinely reliable
pipeline or a not-yet-tested one. This section reports what was observed; it
does **not** claim long-run reliability. The inference scales with the data
window.

### Q3.1 — Per-ticker median and max interval

```sql
WITH gaps AS (
  SELECT
    ticker,
    observed_at,
    observed_at - LAG(observed_at) OVER (
      PARTITION BY ticker ORDER BY observed_at
    ) AS gap
  FROM market_snapshots
)
SELECT
  ticker,
  COUNT(*) FILTER (WHERE gap IS NOT NULL) AS gap_count,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM gap))
    AS median_gap_seconds,
  MAX(gap) AS max_gap
FROM gaps
GROUP BY ticker
ORDER BY ticker;
```

**Raw result:**

```
             ticker              | gap_count | median_gap_seconds | max_gap
---------------------------------+-----------+--------------------+----------
 KXAAAGASED-26NOV03-4.50         |       264 |                900 | 00:15:00
 KXCBDECISIONEU-26JUN11-H25      |       264 |                900 | 00:15:00
 KXCRITICALITY-26AUG-ATOMIC      |       264 |                900 | 00:15:00
 KXECONSTATCORECPIYOY-26JUN-T2.8 |       264 |                900 | 00:15:00
 KXFOMCDISSENTCOUNT-26JUN-0      |       264 |                900 | 00:15:00
 KXHORMUZWEEKLY-26MAY03-T50      |       264 |                900 | 00:15:00
 KXKASHOUT-26APR-AUG01           |       264 |                900 | 00:15:00
 KXMARENTCONTROL-26              |       264 |                900 | 00:15:00
 KXSPACEXCOUNT-26MAY-13          |       264 |                900 | 00:15:00
(9 rows)
```

**Interpretation:** Every ticker has 264 intervals, median 900s (exactly 15 min),
max 15:00 — i.e. *every* gap on *every* ticker is exactly 15 minutes. There is
no per-market polling drift over the observed window.

### Q3.2 — Per-ticker gap distribution by threshold

```sql
WITH gaps AS (
  SELECT
    ticker,
    observed_at - LAG(observed_at) OVER (
      PARTITION BY ticker ORDER BY observed_at
    ) AS gap
  FROM market_snapshots
)
SELECT
  ticker,
  COUNT(*) FILTER (WHERE gap > INTERVAL '15 minutes')  AS gt_15min,
  COUNT(*) FILTER (WHERE gap > INTERVAL '30 minutes')  AS gt_30min,
  COUNT(*) FILTER (WHERE gap > INTERVAL '1 hour')      AS gt_1h,
  COUNT(*) FILTER (WHERE gap > INTERVAL '6 hours')     AS gt_6h,
  MAX(gap) AS max_gap
FROM gaps
WHERE gap IS NOT NULL
GROUP BY ticker
ORDER BY gt_30min DESC, gt_15min DESC;
```

**Raw result:**

```
             ticker              | gt_15min | gt_30min | gt_1h | gt_6h | max_gap
---------------------------------+----------+----------+-------+-------+----------
 KXAAAGASED-26NOV03-4.50         |        0 |        0 |     0 |     0 | 00:15:00
 KXCBDECISIONEU-26JUN11-H25      |        0 |        0 |     0 |     0 | 00:15:00
 KXCRITICALITY-26AUG-ATOMIC      |        0 |        0 |     0 |     0 | 00:15:00
 KXECONSTATCORECPIYOY-26JUN-T2.8 |        0 |        0 |     0 |     0 | 00:15:00
 KXFOMCDISSENTCOUNT-26JUN-0      |        0 |        0 |     0 |     0 | 00:15:00
 KXHORMUZWEEKLY-26MAY03-T50      |        0 |        0 |     0 |     0 | 00:15:00
 KXKASHOUT-26APR-AUG01           |        0 |        0 |     0 |     0 | 00:15:00
 KXMARENTCONTROL-26              |        0 |        0 |     0 |     0 | 00:15:00
 KXSPACEXCOUNT-26MAY-13          |        0 |        0 |     0 |     0 | 00:15:00
(9 rows)
```

**Interpretation:** 0 of 264 intervals per ticker exceed *any* threshold above
the 15-min floor (0/264 = 0.0% at every threshold). No per-market polling
hiccups in the observed window.

### Q3.3 — Per-UTC-date gap distribution

```sql
WITH gaps AS (
  SELECT
    ticker,
    observed_at,
    observed_at - LAG(observed_at) OVER (
      PARTITION BY ticker ORDER BY observed_at
    ) AS gap
  FROM market_snapshots
)
SELECT
  DATE(observed_at AT TIME ZONE 'UTC') AS utc_date,
  COUNT(DISTINCT ticker) FILTER (WHERE gap > INTERVAL '15 minutes')
    AS tickers_with_gt_15min,
  COUNT(*) FILTER (WHERE gap > INTERVAL '15 minutes') AS gt_15min,
  COUNT(*) FILTER (WHERE gap > INTERVAL '30 minutes') AS gt_30min,
  COUNT(*) FILTER (WHERE gap > INTERVAL '1 hour')     AS gt_1h,
  COUNT(*) FILTER (WHERE gap > INTERVAL '6 hours')    AS gt_6h
FROM gaps
WHERE gap IS NOT NULL
GROUP BY utc_date
ORDER BY utc_date;
```

`utc_date` is bucketed on the *later* snapshot of each consecutive pair —
i.e. the date the gap "ended on," matching when ops would notice it.

**Raw result:**

```
  utc_date  | tickers_with_gt_15min | gt_15min | gt_30min | gt_1h | gt_6h
------------+-----------------------+----------+----------+-------+-------
 2026-05-01 |                     0 |        0 |        0 |     0 |     0
 2026-05-02 |                     0 |        0 |        0 |     0 |     0
 2026-05-03 |                     0 |        0 |        0 |     0 |     0
 2026-05-04 |                     0 |        0 |        0 |     0 |     0
(4 rows)
```

**Interpretation:** Zero gaps above the 15-min floor on any UTC date — no
global cron-failure or server-down events visible in the observed window.
The per-ticker breakout (Q3.2) and per-date breakout (Q3.3) are both clean,
so the global-vs-per-ticker pattern question (which dominates?) is moot
here: **neither pattern is present.**

### Q3 summary

Pipeline density is perfect over the observed 2.75-day window: 9/9 tickers
poll exactly every 15 minutes, no per-market drift, no global outages. The
caveat above stands — perfection over 66 hours is consistent with high
reliability *or* with insufficient runtime to surface the failure modes.
The inference strengthens as the data window grows.

## Q4. Earliest feasible T for a 90-day-pre / 90-day-post window

This question is answered by arithmetic, not by a query. Given the Q1
findings — earliest `observed_at` = **2026-05-01 18:45 UTC**, latest =
**2026-05-04 12:45 UTC**, total span = **2.75 days** — the 90/90 window
question is **degenerate**.

For an N-day-pre / N-day-post window, T is feasible iff the data span around T
satisfies both `T ≥ earliest + N` and `T ≤ latest - N` simultaneously. That
requires `latest - earliest ≥ 2N`. With `latest - earliest = 2.75 days`, no
window with N ≥ 2 days has a feasible T today.

### Q4.1 — Feasibility floor at 30/30, 60/60, and 90/90

Assuming continuous collection from 2026-05-01 18:45 UTC onward (i.e. no
multi-day pipeline outage between today and the floor date), the earliest
calendar date on which each window first admits a single feasible T is:

| Window  | Required total span | First feasible date    | T value at that date  |
|---------|---------------------|------------------------|-----------------------|
| 30 / 30 | 60 days             | **2026-06-30 (UTC)**   | 2026-05-31 18:45 UTC  |
| 60 / 60 | 120 days            | **2026-08-29 (UTC)**   | 2026-06-30 18:45 UTC  |
| 90 / 90 | 180 days            | **2026-10-28 (UTC)**   | 2026-07-30 18:45 UTC  |

Days from audit date (2026-05-04) to feasibility floor: 30/30 ≈ 57 days,
60/60 ≈ 117 days, 90/90 ≈ 177 days.

On the floor date itself the feasible T is unique (a single point); as
collection continues past the floor, the upper bound T_max grows by one day
per day while the lower bound T_min stays pinned to `earliest + N`.

If the pipeline experiences a multi-day outage between now and any floor date,
that floor moves out by the outage length — or, depending on Test B's
gap-tolerance rules, by more.

### Q4.2 — Markets with continuous coverage across the full 180-day window

Cannot be answered from data, by construction: no such window exists today.

Two structural notes for when it does:

1. **Row-presence coverage** at the 90/90 floor T = 2026-07-30 will be the
   set of tickers continuously present in `tracked_markets.yml` from
   2026-05-01 through 2026-10-28. The audit cannot enumerate that set in
   advance — it depends on YAML edits made between now and the floor date.
   Today's set is the 9 tickers in the current YAML.
2. **Information-content coverage** is a different and probably more
   important question. Per the YAML `note` fields (treat as approximate;
   `market_metadata` is empty so authoritative resolution dates are not
   available), several tracked markets resolve well before the 90/90 floor
   T or within the post-T window:
   - `KXHORMUZWEEKLY-26MAY03-T50` — weekly, ~2026-05-03 (already past as of audit date)
   - `KXSPACEXCOUNT-26MAY-13` — end of May 2026
   - `KXCBDECISIONEU-26JUN11-H25` — 2026-06-11
   - `KXECONSTATCORECPIYOY-26JUN-T2.8` — June 2026
   - `KXFOMCDISSENTCOUNT-26JUN-0` — June 2026
   - `KXKASHOUT-26APR-AUG01` — by 2026-08-01
   - `KXCRITICALITY-26AUG-ATOMIC` — by August 2026
   - `KXAAAGASED-26NOV03-4.50` — 2026-11-03
   - `KXMARENTCONTROL-26` — November 2026

   Per the Q2 pruning-bias finding, post-resolution rows continue to be
   collected (price pinned at 0/1, no further information). For most tracked
   markets, the post-T half of a 90/90 window centered on 2026-07-30 will
   contain mostly post-resolution rows. Whether that meets Test B's
   information-content requirements is a Test B design question, flagged but
   not answered here.

---

## Headline findings

Findings are labeled by epistemic status:

- **Proved** — code-cited, immune to short-data-window caveats.
- **Observed** — supported only by the 2.75-day data window; not
  generalizable to long-run behavior.
- **Not yet observable** — the audit could not answer with current data.

### 1. Selection bias is the dominant constraint on Test B (Proved)

`tracked_markets.yml` is a static, hand-curated list of 9 markets (see
[Q2(b)](#b-selection-bias--is-the-tracked-universe-representative)).
The collector polls exactly that list; nothing in the codebase mutates it.
This means Test B, whenever it runs, will produce findings *conditional on
the hand-picked universe* — not generalizable to "long-dated low-volatility
prediction markets" as a class.

This is the most strategically important finding of the audit. Every
downstream design decision for Test B should account for it explicitly. A
universe of 9 hand-picked markets is small enough that single-market
idiosyncrasies (settlement quirks, illiquidity, mid-window news) can drive
the headline statistic.

### 2. Pruning bias: pipeline is structurally immune (Proved)

Code-cited at [Q2(a)](#a-pruning-bias--does-the-pipeline-drop-post-resolution-rows):
no status filter on poll, no status filter on insert, no auto-prune on 404,
plain `INSERT … ON CONFLICT DO NOTHING`, no DELETE/TRUNCATE/retention job
anywhere. Post-resolution rows are retained indefinitely. Confirmatory query
returned 0 rows of soft-prune evidence.

### 3. Snapshot density is mechanically perfect over the observed window (Observed)

9/9 tickers, 264 intervals each, every gap exactly 15 minutes, zero gaps over
any threshold above the 15-min floor (see
[Q3](#q3-snapshot-density-and-gaps)). This is consistent with a reliable
pipeline *or* with insufficient runtime to surface failure modes; the
inference scales with the data window.

### 4. `market_metadata` is empty by design omission, not by bug (Proved)

`db.upsert_metadata` is defined ([db.py:137-152](../kalshi_pipeline/db.py#L137-L152))
but never called. No daily-refresh subcommand or scheduled caller exists.
Future work; not a blocker for Test B's price-based queries, but it does
mean authoritative resolution dates, tick sizes, and market metadata are
unavailable for downstream filtering today.

### 5. Post-resolution row retention behavior at scale (Not yet observable)

The Q2(a) code-reading proves the pipeline does not *prune* post-resolution
rows. What the audit *cannot* yet observe is what the Kalshi API returns for
a long-resolved ticker (steady prices? null fields? eventual 404?), because
no tracked market has been resolved during the data window. This will become
observable as soon as the first tracked market resolves and post-resolution
rows accumulate — which begins this week with `KXHORMUZWEEKLY-26MAY03-T50`.

### 6. Window feasibility floors (Proved, conditional on continuous collection)

- 30/30 window: feasible from **2026-06-30** (~57 days out)
- 60/60 window: feasible from **2026-08-29** (~117 days out)
- 90/90 window: feasible from **2026-10-28** (~177 days out)

Each floor moves out by the length of any multi-day pipeline outage between
now and that date.

---

## Decisions to make (next-action list)

The audit surfaces these decisions; it does not make them.

1. **Metadata refresh job.** Build before more snapshot data accumulates,
   so that metadata is captured alongside snapshots rather than retroactively
   reconstructed from `raw_payload`. Decision: when, and as a new subcommand
   of the existing CLI or as a separate cron job?
2. **Selection-bias mitigation.** Decide whether to broaden the YAML universe
   now, accepting that newly-added markets will only have post-add data and
   will therefore not be in the eligible set for the 90/90 floor T = 2026-07-30
   (their pre-T history won't reach back to 2026-05-01). Adding later means
   later feasibility floors for the broader set. Decision: keep at 9 and
   accept the small-N caveat in Test B, or expand and accept staggered
   feasibility per-cohort?
3. **Calendar reminders for window floors.** Set reminders for 2026-06-30
   (30/30), 2026-08-29 (60/60), and 2026-10-28 (90/90). Decision: which
   reminder mechanism, and what (if anything) to verify on each date beyond
   "is the data still flowing?"
4. **Degraded-window early Test B.** Consider whether to run a 30/30 (or
   even shorter) version of Test B at the first feasible date as a dry-run
   for the eventual 90/90 version. Decision: is the methodological signal
   from a 30/30 worth the engineering time, or wait for 90/90?
5. **Test B information-content requirements vs. resolution timing.** Per
   Q4.2, most tracked markets resolve before or shortly after the 90/90
   floor T. Decision: does Test B require live (unresolved) post-T price
   activity, or is post-resolution row presence sufficient? If the former,
   the YAML universe needs to be biased toward markets resolving *after*
   T + 90 days, which conflicts with the small-N hand-picked nature of the
   current set.
