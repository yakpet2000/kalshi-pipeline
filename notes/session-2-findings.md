# Session 2 findings

Notes captured during session 2 (collector + first real run) that should carry forward to session 3 (query layer).

## Parent → sub-ticker resolution

Most user-facing "market" names are actually Kalshi *event* tickers, not market tickers. The collector polls the latter.

Resolution strategy, confirmed empirically against all 9 tracked markets:

1. Try `GET /markets/{ticker}` first.
2. If 200 and `market.ticker == requested_ticker`, the parent is itself a terminal market — done.
3. Otherwise (404 with `{"error": ...}` body in practice), call `GET /events/{ticker}` and match a child by `yes_sub_title` (or `custom_strike` if multiple children share a sub-title).

Empirical pattern: 8 of 9 tickers resolved as events with sub-tickers; 1 (`KXMARENTCONTROL-26`) was terminal. The terminal case is the exception.

## First-ticker latency

The first ticker in each run takes ~150–225ms; subsequent tickers settle to ~80–100ms. Cause is httpx connection setup (TLS handshake, etc.) on the first request. The persistent `httpx.Client` held as an instance attribute inside `KalshiClient` keep-alives the connection across the loop and amortizes the cost.

**Do not refactor `KalshiClient` to per-request `httpx.Client`s.** That would multiply the setup cost by ~9 every run for no benefit.

## Idempotency confirmed

`ON CONFLICT (ticker, observed_at) DO NOTHING RETURNING ticker` correctly suppresses duplicate inserts when the collector is re-run inside the same 15-minute bucket. The two-counter design (`succeeded` vs `inserted`) is the right operational signal: `succeeded=9, inserted=0` means "everything ran fine, but nothing was new" — the expected steady state of a cron retry inside a bucket. Don't collapse them.

## Empty-string-as-unset (still relevant)

Confirmed across the seven new probes that Kalshi returns `""` rather than `null` for unset string fields: `result`, `rules_primary`, `rules_secondary`, `expiration_value`. One additional case observed: `subtitle: "::"` on a single market — looks like a Kalshi-side bug, harmless to us. These all live in `raw_payload` only; if a future query ever filters on them from SQL, use `!= ''` not `IS NOT NULL`.

## Status diversity in events

While probing `/events/KXKASHOUT-26APR`, one sibling market (`KXKASHOUT-26APR-MAY01`) came back as `status="finalized"` with `result="no"` and `settlement_value_dollars="0.0000"`. Confirms `finalized` is a real terminal state we will see in the wild, alongside `active`. Relevant in session 4 when we wire recurring weekly tickers to cron — we should expect to either filter out non-active children or accept that some snapshots will be of already-settled markets.

## Open questions still pending into session 3

### `previous_*_dollars` semantics

`previous_price_dollars`, `previous_yes_bid_dollars`, `previous_yes_ask_dollars` are present and populated on every snapshot, but the *meaning* of "previous" is undocumented. Candidates: prior tick, prior day close, scheduled reset on some interval. The collector now captures these values in every row, so session 3 can analyze them across many ticks to pin down which one it is. Until then, treat them as opaque-but-stored.

### `tick_size` unit ambiguity (unchanged from session 1)

Every market we have polled returns `tick_size = 1`. Working hypothesis remains "deci-cents" (i.e. `tick_size = 1` → $0.001), based on `price_level_structure = "tapered_deci_cent"` and `price_ranges[].step = "0.0010"` on most markets. Cannot verify without observing a market with `tick_size != 1`.

### `custom_strike` schema varies by series

Four shapes seen so far: `{"Date": "..."}`, `{"Value": "..."}`, `{"Company": "..."}`, `{"Action": "..."}`. The key name varies per Kalshi series. Irrelevant to the collector (we store the whole payload as JSONB), but the session 3 query layer will need series-specific paths if we ever filter or group by strike.
