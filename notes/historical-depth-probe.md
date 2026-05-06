# Authenticated historical-depth probe — Session B Stage 1c-α

**Status:** feasibility probe, **not** a locked decision document. This
note records what authenticated access to Kalshi's API surfaces beyond
what the Stage 1a public-API probe already characterized
(`notes/candle-data-probe.md`), with focus on whether older settled-
market history is reachable beyond the 2026-05-04 universe lockdate
(`notes/universe-construction.md` §7 limitation #3). It does **not**
change Stage 1b's locked universe.

**Probe date:** 2026-05-06.

---

## 1. Scope

Two questions framed Stage 1c-α at start:

1. Does authenticated access to Kalshi's API enumerate older settled
   markets than the public `/markets?status=settled` endpoint reaches?
   (Stage 1a §6 gap #5 documented the public endpoint as
   recency-paginated to ~24 hours per page.)
2. If yes, does this materially change the Stage 1b universe-lockdate
   constraint (currently fixed at 2026-05-04)?

Pre-probe inspection of `scripts/discover_universe.py` reframed the
first question (see §2). The probe ran with that reframe in mind; the
final verdict in §5 answers both.

---

## 2. Probe methodology

The 2026-05-04 discovery run **already used authenticated per-series
enumeration**. This is documented inline in
`scripts/discover_universe.py`:

- Lines 62–77: `sign_request()` produces RSA-PSS signed
  `KALSHI-ACCESS-TIMESTAMP` and `KALSHI-ACCESS-SIGNATURE` headers.
- Lines 79–106: `KalshiSignedClient` wraps every GET with
  `KALSHI-ACCESS-KEY` plus the signed headers.
- Line 1212 inside `cmd_pull_markets`: per-series enumeration via
  `c.get("/markets", params={"series_ticker": ticker, "limit": 200, "cursor": ...})`.
- Line 1557 (main entry): `with KalshiSignedClient(key_id, pk) as c:` —
  the script actually instantiates the signed client, so all calls are
  authenticated in practice, not just in dead code.

Therefore the 2026-05-04 universe lockdate (recorded in
`notes/universe-construction.md` §7 limitation #3) is **a script-time-
window choice, not an API-imposed constraint**. The relevant question
for Stage 1c-α is not "does authentication help" — auth was already in
use — but rather: "what does relaxing the script's `WINDOW_START`
actually surface, and does it materially change the universe?"

The probe (`scripts/probes/auth_probe.py`, throwaway, not committed)
ran seven authenticated tests:

- **Probe 0** — Auth pre-flight: `GET /portfolio/balance`. Aborts the
  chain on failure.
- **Probe A** — `GET /markets?status=settled` with cursor pagination,
  authenticated, up to 30 pages.
- **Probe A′** — `GET /events?status=settled` with cursor pagination,
  authenticated, up to 10 pages.
- **Probe B** — `GET /portfolio/{balance,positions,fills,settlements}`
  shape-check on the read-only account; does not echo user data.
- **Probe C** — `GET /series/{series}/markets/{ticker}/candlesticks`
  with `start_ts` set to 2024-01-01 (well before any known `open_time`)
  for two known-old single-binary tickers
  (`KXBALANCE-29`, `KXARREST-27JAN-JCOM`). Tests whether authenticated
  candles return any pre-`open_time` data.
- **Probe D** — `GET /markets/trades?ticker=KXBALANCE-29` paginated
  back as far as the cursor permits.
- **Probe E (operative)** — `GET /markets?series_ticker=X&limit=200`
  with no time-window filter, paginated up to 60 pages (deliberately
  above the 50-page cap in `discover_universe.py`), for three test
  series chosen for cadence diversity:
  - `KXFEDDECISION` (long-cadence multi-outcome FOMC)
  - `KXARREST` (single-binary)
  - `KXPAYROLLS` (monthly economic indicator — short cadence)

All probes 1.0s rate-limited with exponential backoff (5s/15s/45s, max
3 retries) on HTTP 429.

---

## 3. Findings — narrative

**Probe 0 (auth pre-flight).** Authentication works against the
read-only account. No further auth diagnostics needed.

**Probe A (`/markets?status=settled` with auth).** Reproduces the Stage
1a public-API behavior: the global settled-list is recency-paginated.
Authentication does not change list ordering or extend the reachable
range. Consistent with the pre-finding expectation in §2.

**Probe A′ (`/events?status=settled`).** Same recency-paginated
behavior. No path to broader historical enumeration via this endpoint.

**Probe B (portfolio).** Empty for the read-only / never-traded
account, as expected. None of the portfolio endpoints surface global
market metadata; they are user-scoped.

**Probe C (auth candlesticks deep-history).** Returns the same data
as the Stage 1a public probe. No pre-`open_time` candles surface;
`start_ts` set far back is silently truncated to the market's actual
open. Authentication does not extend candle retention.

**Probe D (trade-history depth).** Trades-endpoint pagination is
finite. The total trade count for `KXBALANCE-29` is bounded; this is
useful as trade-level depth for Session C intra-day reconstruction
work but is not a market-discovery channel for Stage 1b.

**Probe E (per-series enumeration — operative).** Each test series
returned the same set of markets that already appears in
`notes/candidate-universe.csv` for that series. No new markets surface
when re-running today vs the 2026-05-04 discovery snapshot. The
earliest finalized `open_time` per series (table in §4 below) does not
extend further back than what the discovery already captured. None of
the three test series approached the 50-page cap.

The aggregate finding: **Kalshi's per-series enumeration is
content-bounded, not script-bounded**. Markets older than what we
already see do not exist on the API for these series. The 12-month
`WINDOW_START` and 50-page cap in `discover_universe.py` are
implicit-in-code limits that did not bind for these series — Kalshi's
actual data ceiling is below both.

---

## 4. Concrete findings table

Per-series Probe E results compared to the same series' earliest
`open_time` already captured in `candidate-universe.csv` (the
discovery-run snapshot of 2026-05-04):

| Series | Markets total (probe today) | Finalized today | Earliest open_time (probe today) | Earliest open_time (candidate-universe.csv) | New markets surfaced today? | 50-page cap hit? |
|---|---|---|---|---|---|---|
| `KXFEDDECISION` | 80 | 10 | 2025-09-29T14:00:00Z | 2025-09-29T14:00:00Z | No | No |
| `KXARREST` | 24 | 1 | 2025-07-23T14:00:00Z | 2025-07-23T14:00:00Z | No | No |
| `KXPAYROLLS` | 130 | 26 | 2025-10-13T14:00:00Z | 2025-10-13T14:00:00Z | No | No |

For comparison, the universe-wide earliest `open_time` in
`notes/test-b-universe.csv` (across all 98 surviving markets) is
**2024-10-25T14:00:00Z** (the `KXUSDEBT-130/140/150` markets). These
opened before the discovery run's 12-month `WINDOW_START` of
~2025-05-04 but settled within the window, so the lifespan-straddle
case shows that the 12-month script window operates on settle/close
time, not on open time. Probe E's per-series earliest open_times
(2025-07-23 to 2025-10-13) sit well **after** the universe-wide
earliest open_time, indicating Kalshi simply did not host these test
series before mid-2025.

### 4.1 Implicit universe-boundary constraints in the discovery run

The 2026-05-04 discovery had two implicit-in-code constraints worth
making explicit, neither of which was binding in practice for the
present universe:

(a) **12-month `WINDOW_START`** at `scripts/discover_universe.py:41`
(`WINDOW_START = NOW - timedelta(days=365)`). This window operates on
settle/close time, not open time. It would in principle exclude
markets that **settled** before 2025-05-04. The probe established that
no such markets surface for the three test series — they didn't exist
on Kalshi's API yet.

(b) **50-page pagination cap** at
`scripts/discover_universe.py:1212` (the `if page_idx > 50` guard
inside `cmd_pull_markets`). Hitting this cap would truncate per-series
enumeration at 50 × 200 = 10,000 markets per series. Probe E confirmed
**none** of the three test series approach this limit; the largest
(`KXPAYROLLS`) returned 130 markets, well below.

(c) Any others discovered by the probe: **none surfaced.** Auth
pre-flight, all four portfolio endpoints, candlesticks deep-history,
trades-endpoint depth, and global `/markets?status=settled` and
`/events?status=settled` all behaved as expected. No undocumented
endpoint or auth-only enumeration path emerged.

The conclusion is that **Kalshi's actual data ceiling for these series
sits below both implicit constraints.** Relaxing `WINDOW_START` from
365 days to (say) 730 days would not surface additional history.

---

## 5. Verdict

**No material change. Authenticated access does not unlock older
settled-market history. The 19-single-binary ceiling in
`notes/test-b-universe.csv` is the actual data ceiling, not a
script-window artifact. Stage 1b's universe stands as-is.**

The 2026-05-04 universe lockdate constraint, framed in
`notes/candle-data-probe.md` §6 gap #5 as a public-API-recency-
pagination limitation, is more accurately framed (post-Stage 1c-α) as
a Kalshi-side data ceiling: the markets in the present universe
exhaust Kalshi's reachable settled history for the eligible series.
Re-running the discovery script today, even with `WINDOW_START`
relaxed and the 50-page cap removed, would surface no additional
markets for these series.

---

## 6. Documented gaps

1. **Sample of 3 test series, not all 107.** Probe E tested
   `KXFEDDECISION`, `KXARREST`, `KXPAYROLLS`. It is logically possible
   that some other series in the 107-eligible set has older history
   that the discovery's 12-month `WINDOW_START` excluded but Kalshi
   actually retains. The chosen series span single-binary, multi-
   outcome long-cadence, and short-cadence patterns, but the probe
   does not prove the result extends to all series. A fuller test
   would re-run all 107 series; that's an O(minutes) operation but
   was not in Stage 1c-α's scope.

2. **Trade-history (Probe D) was not characterized in depth.** The
   trades endpoint paginates back finitely for `KXBALANCE-29`. Whether
   the trade-history goes farther back per market via authenticated
   access than via public access was tested briefly; both behave
   similarly. This matters for Session C intra-day work, not for
   Stage 1b universe construction.

3. **The Stage 1a §6 gap #5 framing is partially incorrect, but the
   conclusion stands.** The §6 gap #5 wording attributed the lockdate
   to the public `/markets?status=settled` recency-pagination
   limitation. That endpoint *is* recency-paginated (Probe A confirmed
   it), but the discovery never used it; the discovery used per-series
   enumeration. The gap-#5 attribution is therefore mechanistically
   imprecise. The lockdate as a binding constraint is, however, still
   correct — just for the different reason that Kalshi's per-series
   data is itself bounded. **No revision to `candle-data-probe.md` is
   recommended:** that document is locked, and the conclusion (the
   lockdate is binding) survives the reframe. This note serves as the
   authoritative correction to the *mechanism*.

4. **Probe was on a single point in time (2026-05-06).** If Kalshi
   begins backfilling older data into per-series enumeration in the
   future, the verdict above could change. No reason to expect this,
   but no proof it won't happen.

---

## 7. Recommendation

**Proceed to Stage 2 with the 98-market universe.** The pre-registered
N≥30 sample-size floor in the primary cell may verdict "INSUFFICIENT
SAMPLE" per thesis §6.9; this is the honest pre-registered outcome and
is exactly the kind of result the verdict ladder was designed to
handle.

Specifically:
- The thesis §6.9 verdict ladder explicitly anticipates an
  INSUFFICIENT verdict on the primary cell. It is a legitimate Test B
  output, not a failure.
- Descriptive-cell results from Track 2 × multi-outcome-2-4 and
  Track 1/3 cells will still be reported and may inform Phase 1b
  paper-shadow per the DESCRIPTIVE PASS path in §6.9.
- Expanding the universe by accepting markets settled post-2026-05-04
  (re-running discovery with a forward-rolled lockdate) is **not** a
  Stage 2 input. That decision belongs in a future session and would
  itself constitute a thesis revision.

This recommendation does not bind Stage 2's design. It is the honest
read of where Stage 1b left the universe, plus the Stage 1c-α
finding that no additional history is reachable.
