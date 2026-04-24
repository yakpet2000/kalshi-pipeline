# Session 1 findings

Notes captured during session 1 (repo skeleton) that should carry forward to session 2 (collector).

## Tick size unit ambiguity

`tick_size` came back as the integer `1` in the one market we probed. Neighboring fields disagree on the unit:

- `response_price_units = "usd_cent"` → suggests `tick_size = 1` means $0.01.
- `price_level_structure = "deci_cent"` → suggests tick granularity is $0.001.
- `price_ranges[0].step = "0.0010"` → matches deci-cent.

**Working hypothesis:** `tick_size` is in deci-cents, so `tick_size = 1` = $0.001. Needs verification against markets with `tick_size != 1` before the stale-price detector relies on it.

## Empty-string-as-unset fields

Kalshi returns `""` (not `null`) for several fields when unset: `result`, `rules_primary`, `rules_secondary`, `expiration_value`. These live in pydantic extras today — if we ever filter on them from SQL, use `!= ''` rather than `IS NOT NULL`; the JSONB will contain empty strings, not JSON null.

Declared datetime fields (`updated_time`, `open_time`, `close_time`, `expected_expiration_time`) are defended by the `_empty_string_to_none` validator on `Market` so pydantic parsing doesn't blow up.

## Envelope shapes

- `GET /markets?limit=N` → `{"cursor": "...", "markets": [...]}`
- `GET /markets/{ticker}` → `{"market": {...}}`

Only the detail envelope has a pydantic model (`MarketDetailResponse`). Add a list-envelope model in session 2 if the collector moves from per-ticker detail calls to the batch endpoint.

## Sample bias

The one market we curled was a multi-leg aggregate (`KXMVESPORTSMULTIGAMEEXTENDED-…`) with fields like `mve_selected_legs`, `custom_strike`, and multi-row `price_ranges`. Simpler single-outcome markets likely have a narrower shape. **Session 2 should probe a plain single-outcome market** (e.g. one NBA or MLB game outcome) before declaring the `Market` model universal. `extra="allow"` should prevent hard failures, but the required-field set may need tuning.

## Biggest-movers query has a free shortcut

Every snapshot response already includes `previous_price_dollars`, `previous_yes_bid_dollars`, `previous_yes_ask_dollars`. The session-3 biggest-movers query can diff `last_price_dollars` against `previous_price_dollars` within a single row — no self-join to the prior snapshot is needed.

Open question: the semantics of "previous" aren't documented in the payload (prior day close? last tick? reset on some schedule?). Session 2 should log these across several ticks to pin down the meaning before session 3's query depends on it.
