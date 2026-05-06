# Candle data feasibility probe — Session B Stage 1a

**Status:** feasibility probe, **not** a locked decision document. This
note records what the public Kalshi API returns for daily-candle data and
documents the gaps the Test B simulator will need to handle. It gates
Stage 1b: if daily candles were unavailable, the test methodology would
need to be rethought before any simulator code was written.

**Scope:** answers six empirical questions about whether the data Test B
needs (per `notes/investment-thesis.md` §2 daily-candle resolution) is
reachable, complete, and clean for settled Kalshi markets.

**Probe date:** 2026-05-06. All raw responses saved during drafting under
`/tmp/kalshi_candle_probe/` for auditability; not committed.

---

## 1. Endpoint, parameters, authentication

**Endpoint:** `GET /series/{series_ticker}/markets/{ticker}/candlesticks`
on base URL `https://api.elections.kalshi.com/trade-api/v2/`.

**Query parameters (all required):**
- `start_ts` — Unix timestamp (integer seconds, UTC)
- `end_ts` — Unix timestamp (integer seconds, UTC)
- `period_interval` — integer, **in minutes**. `1440` = daily; `60` =
  hourly; `1` = minute (also tested but not used for v0.1).

**Authentication:** none required. Public endpoint, accessible
unauthenticated via `httpx`.

A request without `start_ts` returns HTTP 400:
`{"msg":"Query argument start_ts is required, but not found"}`.

---

## 2. Probe summary — six concrete markets

| Label | Ticker | Age | Candles returned | Volume>0 days |
|---|---|---|---|---|
| recent_30d_mid | `KXBALANCE-29` | 30d settled, 16mo lifespan | 450 | 206 |
| older_68d | `KXTRUMPMEETING-27JAN01-NZMAM` | 68d settled (per CSV) | **404 — unreachable** | — |
| high_volume | `KXFEDCHAIRNOM-29-JS` | 61d settled, 14.5mo lifespan | 372 | 157 |
| low_volume | `KXNDPLEADER-26APR01-TMCQ` | 37d settled, ~6mo lifespan | 168 | 1 |
| mid_macro | `KXNASDAQ100MINY-26DEC31H1600-T23000.01` | 16d settled, ~2mo lifespan | 61 | 40 |
| walkthrough_iran | `KXNEXTIRANLEADER-45JAN01-MKHA` (thesis A.1) | ~60d settled, 64d lifespan | 59 | 50 |
| walkthrough_costarica | `KXMOVCOSTARICAPRESR1-26FEB01-P2` (thesis A.2) | ~94d settled, 13d lifespan | 11 | 1 |
| walkthrough_canada | `KXCANADALIBERAL-26DEC31` (thesis A.3) | ~21d settled, 114d lifespan | 92 | 38 |

7 of 8 tickers returned candle data. 1 ticker (KXTRUMPMEETING from the
candidate-universe.csv) was unreachable on both `/markets/{ticker}` and
`/series/.../candlesticks` despite being listed as `finalized` in our
universe. See gap #2 in §6 below.

---

## 3. Schema of returned candles

Top-level response shape:
```json
{
  "ticker": "...",
  "candlesticks": [<candle>, ...]
}
```

Each `<candle>`:
```json
{
  "end_period_ts": 1735966800,
  "open_interest_fp": "1.00",
  "volume_fp": "1.00",
  "price": {
    "open_dollars":  "0.1500",
    "high_dollars":  "0.1500",
    "low_dollars":   "0.1500",
    "close_dollars": "0.1500",
    "mean_dollars":  "0.1500",
    "previous_dollars": "0.1500"
  },
  "yes_ask": { "open_dollars": "...", "high_dollars": "...",
               "low_dollars":  "...", "close_dollars": "..." },
  "yes_bid": { "open_dollars": "...", "high_dollars": "...",
               "low_dollars":  "...", "close_dollars": "..." }
}
```

Field types and conventions:
- `end_period_ts` — Unix epoch seconds, integer. Marks the **end** of the
  candle bucket. See §5 for the bucket-boundary subtlety.
- `volume_fp`, `open_interest_fp` — string decimals, 2 dp. Match the
  `_fp` convention in `CLAUDE.md`.
- `price.{open,high,low,close,mean,previous}_dollars` — string decimals,
  4 dp. Match the `_dollars` convention. Only `_dollars` fields are used
  by Test B; the legacy integer-cents fields are not present in candles.
- `yes_ask`, `yes_bid` — bid/ask book state at candle boundaries (open/
  close) and extremes (high/low). Reported even on zero-volume days.

The fill model in `notes/maker-fill-model.md` consumes
`price.high_dollars`, `price.low_dollars`, and `volume_fp`. All three are
present and clean on volume>0 days; see §4 for zero-volume behavior.

---

## 4. Zero-volume candle behavior

On days with `volume_fp = "0.00"` the `price` object is **partial or
empty**. Two distinct shapes occur:

**Shape A** — partial: `price = {"previous_dollars": "0.1500"}`.
Returned when the market has traded at some prior point but not on this
day. Only the rolled-forward last-trade price is present.

**Shape B** — empty: `price = {}`. Returned when the market has never
traded since open, or there is no prior close to roll forward.

**Implication for the locked fill model.** The fill model
(`notes/maker-fill-model.md`) requires `volume > 0` for a fill, so both
zero-volume shapes correctly produce `filled = False` regardless of
missing OHLC. The simulator must defensively handle missing
`high_dollars` / `low_dollars` keys — it cannot assume they are always
present — but the locked rule's volume guard means this is safe by
construction. No fill model revision required.

Sample concrete examples:
- `KXNDPLEADER-26APR01-TMCQ` first candle (2025-09-12, no trades ever):
  `price = {}, volume_fp = "0.00", open_interest_fp = "0.00"`.
- `KXBALANCE-29` 2nd candle (rolled-forward after first day's trade):
  `price = {"previous_dollars": "0.1500"}, volume_fp = "0.00"`.

---

## 5. Critical finding — daily bucket boundary is 00:00 ET, not 00:00 UTC

Across every probed market, daily candle `end_period_ts` values land at
either **04:00 UTC** (during US daylight saving time, EDT = UTC−4) or
**05:00 UTC** (during standard time, EST = UTC−5). For example:
- `KXNEXTIRANLEADER-45JAN01-MKHA` first candle ends 2026-01-07T05:00:00Z
  (winter, EST).
- `KXCANADALIBERAL-26DEC31` last candle ends 2026-04-15T04:00:00Z (after
  US DST began on 2026-03-08, EDT).

This means Kalshi's "daily" bucket is **midnight US Eastern Time to
midnight US Eastern Time**, not midnight UTC. Thesis §2 specifies the
strategy "checks for entry conditions once per UTC day at 00:00 UTC,
immediately after the previous day's candle has finalized" — but the
candle that has just finalized at 00:00 UTC covers the prior **partial**
US Eastern day (since the ET-day bucket runs ~04:00 UTC to ~04:00 UTC).

**Resolution.** The simulator will fire its once-per-day check at the
candle-finalization moment — 04:00 UTC during EDT, 05:00 UTC during EST
— rather than at literal 00:00 UTC. This is a 4–5 hour offset from the
thesis §2 wording but is the closest faithful realization of the
thesis's intent ("once per UTC day, immediately after the previous
day's candle has finalized") given Kalshi's ET-bucketed candle
structure. The thesis is **not** amended; its "00:00 UTC" wording is
preserved as a record of intent. The fill model
(`notes/maker-fill-model.md`) is **not** amended. This document is the
authoritative record of the offset and its handling.

**Date-labeling convention.** A fill recorded against a candle ending
at `YYYY-MM-DDT04:00:00Z` (EDT) or `YYYY-MM-DDT05:00:00Z` (EST) is
labeled with the **calendar date of the ET bucket the candle covers**
— i.e., the date the candle period mostly falls within in US Eastern
Time — not the UTC date of the finalization moment. Concretely: a
candle with `end_period_ts` corresponding to 2026-04-15T04:00:00Z
(EDT) covers the ET-day 2026-04-14 (00:00 ET → 24:00 ET on April 14)
and is labeled `2026-04-14`. This convention is locked here so Stage
2 (and any later analysis) does not have to invent one.

---

## 6. Documented gaps and gotchas

1. **`series_ticker` is null in `/markets/{ticker}` for finalized
   markets.** The candlesticks endpoint requires the series_ticker in
   the URL path, but `GET /markets/{ticker}` returns `series_ticker:
   null` once a market is finalized. Workarounds: (a) read
   `series_ticker` from `notes/candidate-universe.csv` (which captured
   it when markets were active), or (b) derive from the ticker prefix
   (`ticker.split('-')[0]`) — works for every probed ticker but is
   undocumented behavior, not a guarantee.

2. **Some finalized tickers are unreachable.** `KXTRUMPMEETING-27JAN01-
   NZMAM` is listed as `finalized` in our candidate-universe.csv but
   returns HTTP 404 from both `/markets/{ticker}` and the candlesticks
   endpoint. 1 of 8 probed markets (12.5%); for a 1,256-row universe
   that could be ~150 markets unreachable. The cause is unclear — could
   be ticker-format change, market deletion, or per-market data-
   retention policy. **The Stage 1b simulator must treat unreachable-
   from-API as a real failure mode and exclude such markets from the
   universe at run time, recording the count as a diagnostic.**

3. **Daily bucket ends at 00:00 ET, not 00:00 UTC.** See §5 for the
   resolution: the simulator fires at candle-finalization (04:00/05:00
   UTC) and uses the ET-bucket calendar date as the fill-date label.
   No thesis or fill-model amendment.

4. **Zero-volume candles have partial or empty `price` objects.** See
   §4. The simulator must guard against missing `high_dollars` /
   `low_dollars` keys. The locked fill model's volume guard makes this
   safe by construction — no revision needed.

5. **`notes/candidate-universe.csv` is the sole source of truth for
   Test B's universe.** The `/markets?status=settled&cursor=...`
   endpoint orders by recency, returning only markets settled within
   the last ~24 hours per page; paginating 15 deep during this probe
   returned only eSports daily recurring contracts. **Fresh API
   enumeration cannot rebuild the universe of markets settled more
   than ~24 hours ago.** Therefore the universe lockdate is effectively
   **2026-05-04** — the discovery run's completion date as recorded in
   `notes/universe-discovery.md` — not the date Stage 1b runs. Markets
   that settled between 2026-05-04 and Stage 1b's run date are not
   retrievable for the universe; their absence is mechanical, not a
   selection bias. This constraint is stronger than a "minor gotcha":
   it fixes the universe lockdate at the discovery-run date and
   forecloses any rebuild-from-API path.

6. **Maximum confirmed historical retention: 16 months.**
   `KXFEDCHAIRNOM-29-JS` opened 2024-12-18 and returned 372 daily
   candles spanning its full lifetime today (probe date 2026-05-06).
   This is sufficient for the v0.1 universe (which contains only
   markets settled within the last 68 days per
   `notes/candidate-universe.csv`). Longer retention is not required by
   Test B.

7. **Probe volume statistics across the 7 reachable markets.** Median
   volume>0 days: 38 of 92 (KXCANADALIBERAL). Range: 1/168
   (KXNDPLEADER, low-volume reference) to 206/450 (KXBALANCE-29). The
   fill model's volume guard will reject the majority of resting-day
   evaluations on low-volume markets — this is the intentional
   permissive-but-not-naive behavior locked in
   `notes/maker-fill-model.md` §3.

---

## 7. Verdict

**Conditional YES.** The daily-candle data Test B requires is reachable,
sufficiently deep historically (16+ months retention confirmed), and has
the schema needed by the locked fill model. The endpoint is
unauthenticated and stable.

**Conditions before Stage 1b begins:**
- (a) Acknowledge §5's documented timing reframe — the simulator fires
  at candle-finalization (04:00 UTC EDT / 05:00 UTC EST) rather than
  at literal 00:00 UTC, and uses the ET-bucket calendar date as the
  fill-date label.
- (b) Accept that ~10–15% of finalized tickers may be unreachable from
  the live API and that the simulator excludes them at run time, with
  the exclusion count reported as a Stage 1b diagnostic.

If conditions (a) and (b) are accepted, Stage 1b can proceed. If
either is unacceptable, the test methodology needs to be rethought
before simulator code is written.
