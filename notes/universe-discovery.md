# Universe discovery — Kalshi candidate set

Run date: 2026-05-04 UTC. Window: last 12 months (2025-05-04 → 2026-05-04).

## Executive summary

- **Total candidates: 6,550** across **715** series (from a kept-series set of 2,805 out of 9,905 total Kalshi series).
- **Liquidity is the binding constraint.** Of 6,550 candidates, only **390 (6.0%) clear the $10K mid-market notional floor**, and only **134 (2.0%) clear $50K**. Test B sample-size design should treat $10K+ as the practical universe size, not 6,550.
- **Status mix:** open=5,245, settled=1,256, closed=49.
- **Label mix:** macro=3,283, geopolitics=782, geopolitics_uncertain=2,485 (uncertain bucket needs human review; see inspection lists below).
- **Duration filter dominates:** of 57,252 raw markets across kept series, 50,618 (88%) failed the `close_time - open_time >= 30d` requirement. This reflects the daily/weekly recurring nature of Kalshi's macro and election series.

## Methodology

### API endpoints used
- `GET /exchange/status` — auth verification.
- `GET /series?limit=200&include_volume=true&include_product_metadata=true` — full /series catalog (one page returned 9,905 series; cursor was null).
- `GET /markets?series_ticker=<X>&limit=200` (paginated) — per kept series, no status filter (returns all statuses; we partition client-side).

### Auth
- RSA-PSS over `(timestamp + method + path)`. The signed message uses the **full** path including `/trade-api/v2/...`, not the post-base relative path. This was the one-line gotcha in the auth helper.
- Read-only key. No production-pipeline state was touched (no DB writes, no edits to `tracked_markets.yml`, no calls to `/portfolio/*`).

### API quirks worth documenting (these will bite future-you)

1. **`?status=settled` returns markets where `status='finalized'`.** The query param value and the response value differ — `settled` is the API's filter input, but `finalized` is the response status string. Both samples in our two-mode probe had `status: 'finalized'`.
2. **`settle_time` is never populated on the response.** The OpenAPI spec lists this field but the actual response uses **`settlement_ts`** instead. Use that. In our CSV, we store it under the column name `settle_time` to match the user-facing task spec, but it is sourced from `settlement_ts`.
3. **`series_ticker` and `category` are NOT echoed on `/markets` responses, even when you filter by `?series_ticker=X`.** Both come back as `null`. We attach `series_ticker` client-side from the filter we passed, and we get `category` from a separate `/series` join.
4. **`occurrence_datetime` vs `close_time`.** They differ on at least one sample by ~3 hours (close_time = trading-window close; occurrence_datetime = trigger event). Test B precision-sensitive logic should pre-register which is the correct stop-trading boundary.
5. **Default sort of `?status=X` is newest-first.** Page 1 is dominated by recently-settled MVE sports markets when no series filter is applied. After 5 pages × 200 markets = 1,000 settled markets, we still hadn't reached anything older than the most recent two weeks. This means **global enumeration of old archive markets via `/markets` is not viable**; per-series queries are the only path. Implication: any market in the kept-series set that resolved >12 months ago is *technically* still reachable by ticker but not by enumeration. Our 'outside 12-month window: 0' exclusion count is a side-effect of this enumeration bias, not evidence the API gave us 12 months of history.
6. **`status='active'` markets have no `settle_time`/`settlement_ts`.** Expected; settlement timestamps populate only after the market resolves.
7. **`last_price_dollars` for finalized markets does NOT pin to 0/1 by `result`.** It's the last *trade* price, which can be off-mid. For settled markets where no trades occurred (volume = 0), it's `0.0000`, regardless of `result`.
8. **`open_interest_fp` persists post-finalize.** Doesn't drop to 0 on settle.
9. **`?status=closed`** is a brief settlement-window state. We saw only 49 markets in this state across the entire kept-series set.

### Filter pipeline (applied in order)
1. Status: include `active` (open), `closed`, `finalized` (settled). Drop `unopened`.
2. Both `open_time` and `close_time` non-null.
3. `(close_time - open_time) >= 30 days`.
4. Time window:
   - `finalized` → `settlement_ts` (or fallback to `expiration_time`) within last 12 months.
   - `closed` → `close_time` within last 12 months.
   - `active` → no time filter.
5. Multivariate exclusion: drop tickers containing `KXMVE` or with non-null `mve_collection_ticker`. (Note: at the per-series stage, multivariate series are in the `Exotics` category, which is hard-excluded — so this is defensive only.)

### Category mapping rules
Kalshi has 19 distinct categories across 9,905 series (one was `(null/empty)`). Three buckets:

**Hard-include as `macro`:** `Economics` (514 series), `Financials` (176), `Commodities` (47).

**Keyword-classified, default-keep ambiguous as `geopolitics_uncertain`:** `Politics` (1,844), `Elections` (1,291), `World` (142), `(null/empty)` (43).

**Hard-exclude:** `Entertainment` (2,396), `Sports` (1,827), `Mentions` (345), `Companies` (356), `Climate and Weather` (269), `Crypto` (225), `Science and Technology` (204), `Health` (111), `Social` (63), `Transportation` (40), `Exotics` (10), `Education` (2).

**Permissive-passthrough counts** (would have been included as macro/geo under permissive classification, but kept out of the final universe per user direction):
- `Companies`: **12 series**
- `Health`: **10 series**

### Keyword classifier
Applied to `Politics`, `Elections`, `World`, `(null/empty)` series. Classifier checks the title and series_ticker (case-insensitive concatenation) against three keyword lists (macro, foreign-policy, foreign-election + foreign country names). If multiple match, macro wins; otherwise foreign-policy or foreign-election. US-domestic markers (state codes, state-level government roles, US-only political bodies) drop the series. Series matching none of the include-lists are kept with subtag `default_keep_uncertain` per user direction (the keyword lists are first-guess heuristics; default-drop would hide errors).

### Liquidity metric
Mid-market notional: `open_interest_value = open_interest_fp × last_price_dollars`. Reasoning: max-payout (`OI × $1`) overstates real exposure on out-of-the-money markets (a 5¢ market with 100K OI has $5K of real exposure, not $100K). Mid-market reflects what a trader would actually be facing.

**Fallbacks for null/zero last_price:**
- 541 markets used `(yes_bid + yes_ask) / 2` (last_price was null or zero but a quoted spread existed).
- 74 markets had no usable price; `open_interest_value` set to 0, tiered as `<$10K`.
- 0 markets had null `open_interest_fp`.

### Edge cases captured during the discovery run
- **MVE leakage check.** No MVE markets reached the candidate set. The `Exotics` category in Kalshi's series taxonomy contains exactly 10 MVE-collection meta-series, and our hard-exclude rule drops them at the series stage.
- **Sub-titles with stray separators (`subtitle: "::"`).** Carry-over from session-2 findings; harmless to us, captured as-is.
- **Series with `category=null`** (43 series). Keyword-classified along with the named ambiguous categories.
- **`null open_time`/`null close_time`.** 0 candidates excluded for either reason. Every market that reached the filter pipeline had both fields populated.
- **50-page pagination cap.** Two series hit the cap (`KXNASDAQ100U`, `KXINXU`). These are short-duration NASDAQ100 / INX Unit markets that would have failed the 30-day duration filter anyway. Not material to the candidate set.

## Headline summary table

### Total candidates by liquidity tier

| tier | count | share |
| --- | --- | --- |
| <$10K | 6,160 | 94.0% |
| $10K-$25K | 157 | 2.4% |
| $25K-$50K | 99 | 1.5% |
| $50K-$100K | 66 | 1.0% |
| $100K+ | 68 | 1.0% |

### Status × label × tier (non-zero cells)

| status | label | tier | count |
| --- | --- | --- | --- |
| open | macro | <$10K | 2,347 |
| open | macro | $10K-$25K | 44 |
| open | macro | $25K-$50K | 26 |
| open | macro | $50K-$100K | 10 |
| open | macro | $100K+ | 11 |
| open | geopolitics | <$10K | 599 |
| open | geopolitics | $10K-$25K | 16 |
| open | geopolitics | $25K-$50K | 10 |
| open | geopolitics | $50K-$100K | 9 |
| open | geopolitics | $100K+ | 6 |
| open | geopolitics_uncertain | <$10K | 2,068 |
| open | geopolitics_uncertain | $10K-$25K | 44 |
| open | geopolitics_uncertain | $25K-$50K | 23 |
| open | geopolitics_uncertain | $50K-$100K | 15 |
| open | geopolitics_uncertain | $100K+ | 17 |
| closed | macro | <$10K | 25 |
| closed | geopolitics | <$10K | 3 |
| closed | geopolitics_uncertain | <$10K | 21 |
| settled | macro | <$10K | 715 |
| settled | macro | $10K-$25K | 36 |
| settled | macro | $25K-$50K | 31 |
| settled | macro | $50K-$100K | 24 |
| settled | macro | $100K+ | 14 |
| settled | geopolitics | <$10K | 128 |
| settled | geopolitics | $10K-$25K | 4 |
| settled | geopolitics | $25K-$50K | 1 |
| settled | geopolitics | $50K-$100K | 3 |
| settled | geopolitics | $100K+ | 3 |
| settled | geopolitics_uncertain | <$10K | 254 |
| settled | geopolitics_uncertain | $10K-$25K | 13 |
| settled | geopolitics_uncertain | $25K-$50K | 8 |
| settled | geopolitics_uncertain | $50K-$100K | 5 |
| settled | geopolitics_uncertain | $100K+ | 17 |

### Status × label (marginals)

| status | macro | geopolitics | geopolitics_uncertain |
| --- | --- | --- | --- |
| open | 2,438 | 640 | 2,167 |
| closed | 25 | 3 | 21 |
| settled | 820 | 139 | 297 |

### Kalshi source category × label (post-filter)

| Kalshi category | macro | geopolitics | geopolitics_uncertain | total |
| --- | --- | --- | --- | --- |
| Commodities | 17 | 0 | 0 | 17 |
| Economics | 2,963 | 0 | 0 | 2,963 |
| Elections | 113 | 585 | 1,658 | 2,356 |
| Financials | 134 | 0 | 0 | 134 |
| Politics | 56 | 197 | 816 | 1,069 |
| World | 0 | 0 | 11 | 11 |

## Distribution stats

### Time-to-resolution distribution (candidates)

| bucket | count |
| --- | --- |
| 30-60d | 1,090 |
| 60-90d | 325 |
| 90-180d | 755 |
| 180-365d | 1,373 |
| 365d+ | 3,007 |

### Open-interest mid-market notional distribution (candidates)

| bucket | count |
| --- | --- |
| <$1K | 5,097 |
| $1K-$10K | 1,063 |
| $10K-$25K | 157 |
| $25K-$50K | 99 |
| $50K-$100K | 66 |
| $100K-$500K | 55 |
| $500K+ | 13 |

### Settle-date distribution (settled bucket only)

| YYYY-MM | count |
| --- | --- |
| 2026-02 | 19 |
| 2026-03 | 547 |
| 2026-04 | 574 |
| 2026-05 | 116 |

## Elections bucket: foreign vs US-domestic split

**Series-level** (1,291 series in `Elections` category, before market-pull):

| classification | series count |
| --- | --- |
| US-domestic (dropped at series stage) | 691 |
| keyword_geo_foreign_election | 167 |
| keyword_geo_foreign_policy | 23 |
| keyword_macro (re-tagged macro) | 22 |
| default_keep_uncertain | 388 |

**Market-level** (Elections markets that survived all filters):

| classification | market count |
| --- | --- |
| foreign | 585 |
| uncertain (default-keep) | 1,771 |
| US-domestic | 0 (dropped at series stage) |

## Inspection lists (for human keep/drop calls)

These lists are sorted descending by total mid-market notional across all candidate markets in the series. Series with no $10K+ candidates are not realistically worth manual inspection time and are count-summarized only. The CSV (`notes/candidate-universe.csv`) has the long tail.

### All `geopolitics_uncertain` series (top 50 by notional)

Total series in this set: **378**. With $10K+ aggregate mid-market notional: **87**. Below $10K: **291** (count-only; see CSV for individual rows).

Top 50 by aggregate notional:

| series_ticker | title | Kalshi category | subtag | candidate markets | total mid-market notional |
| --- | --- | --- | --- | --- | --- |
| `KXGOVTSHUTLENGTH` | How long will the next government shutdown last? | Politics | default_keep_uncertain | 19 | $3,306,310 |
| `CONTROLH` | House winner | Elections | default_keep_uncertain | 2 | $2,949,966 |
| `KXPRESPERSON` | Pres person | Elections | default_keep_uncertain | 25 | $1,386,287 |
| `KXTRUMPADMINLEAVE` | Who will leave the Trump administration | Politics | default_keep_uncertain | 35 | $1,246,032 |
| `CONTROLS` | Senate winner | Elections | default_keep_uncertain | 2 | $1,116,374 |
| `KXSENATEMED` | MED | Elections | default_keep_uncertain | 9 | $905,917 |
| `KXTRUMPOUT27` | Trump out as President? | Elections | default_keep_uncertain | 5 | $576,960 |
| `KXGOLDCARDS` | Gold cards sold | Politics | default_keep_uncertain | 6 | $520,937 |
| `KXNEXTAG` | Next AG | Politics | default_keep_uncertain | 22 | $397,186 |
| `KXHOUSERACE` | House Race Winner? | Elections | default_keep_uncertain | 720 | $343,419 |
| `KXARREST` | ARRESTS | Politics | default_keep_uncertain | 24 | $285,281 |
| `KXGA14S` | Who will win the GA-14 special election? | Elections | default_keep_uncertain | 29 | $242,311 |
| `KXSENATEILD` | ILD | Elections | default_keep_uncertain | 14 | $201,217 |
| `KXLAGODAYS` | Mar-a-Lago trips | Politics | default_keep_uncertain | 15 | $195,731 |
| `KXREDISTRICTING` | Redistricting | Elections | default_keep_uncertain | 24 | $195,255 |
| `KXGREENTERRITORY` | Greenland acquisition | Politics | default_keep_uncertain | 3 | $182,368 |
| `KXTRUMPMEET` | Who will Trump meet? | Politics | default_keep_uncertain | 34 | $162,878 |
| `KXGREENLANDPRICE` | How much will Greenland be acquired for? | Politics | default_keep_uncertain | 8 | $137,749 |
| `KXPRESPARTY` | Party winning presidency | Elections | default_keep_uncertain | 2 | $124,481 |
| `KXMJSCHEDULE` | MJ schedule | Politics | default_keep_uncertain | 4 | $113,053 |
| `KXMUSKOAI` | Elon Win vs Open AI | Politics | default_keep_uncertain | 1 | $111,780 |
| `KXTRUMPCOUNTRIES` | What countries will Trump visit this year? | Politics | default_keep_uncertain | 24 | $111,571 |
| `KXLEAVESTARMER` | Keir Starmer out? | Politics | default_keep_uncertain | 4 | $108,124 |
| `KX2028DRUN` | 2028 D running | Elections | default_keep_uncertain | 35 | $106,200 |
| `KXTRUMPMEETING` | Who will Trump meet this year? | Politics | default_keep_uncertain | 20 | $90,547 |
| `KXFEDERALCHARGE` | Who will be charged with a federal crime | Politics | default_keep_uncertain | 33 | $90,079 |
| `KXSUPERBOWLWHITEHOUSE` | WILL THE WINNERS OF THE PRO FOOTBALL CHAMPIONSHIP GO TO THE WHITE HOUSE?  | Politics | default_keep_uncertain | 1 | $74,975 |
| `KXGORDONDENTONBY` | Who will win the 2026 Gordon and Denton by-election? | Elections | default_keep_uncertain | 9 | $71,264 |
| `KXDHSCOMPONENT` | Which DHS components will be funded? | Politics | default_keep_uncertain | 5 | $69,095 |
| `KXCOSTARICAPRES` | Costa Rica presidency | Elections | default_keep_uncertain | 9 | $63,166 |
| `KXBLUEWAVECOMBO` | Will there be a blue wave? | Elections | default_keep_uncertain | 1 | $57,323 |
| `KXDJTWHDINNER` | DJT WH Correspondents dinner | Politics | default_keep_uncertain | 2 | $54,999 |
| `KXTRUMPPARDONS` | Who will Trump pardon? | Politics | default_keep_uncertain | 49 | $54,334 |
| `KX2028RRUN` | 2028 R running | Elections | default_keep_uncertain | 29 | $53,827 |
| `KXCAWEALTHTAX` | Will the California billionaire wealth tax initiative pass? | Elections | default_keep_uncertain | 1 | $53,503 |
| `KXSENATESCR` | SCR | Elections | default_keep_uncertain | 4 | $52,486 |
| `KXCRYPTOSTRUCTURE` | Crypto market structure | Politics | default_keep_uncertain | 7 | $51,763 |
| `KXTRUMPAPPROVALBELOW` | How low will Trump's approval get this year? | Politics | default_keep_uncertain | 8 | $51,385 |
| `KXTRUMPREMOVE` | Trump removed | Politics | default_keep_uncertain | 1 | $51,116 |
| `KXTRUMPENDORSE` | who will Trump endorse | Elections | default_keep_uncertain | 14 | $50,256 |
| `KXCITRINI` | Will the Citrini scenario materialize? | Elections | default_keep_uncertain | 1 | $48,867 |
| `KXINSURRECTION` | Insurrection | Politics | default_keep_uncertain | 4 | $48,830 |
| `KXBADWUR` | Baden-Württemberg Parliamentary Election Winner | Elections | default_keep_uncertain | 3 | $46,734 |
| `KXUAPFILES` | UAP files | Politics | default_keep_uncertain | 1 | $46,732 |
| `KXBEZELD` | Will Rolex discontinue production of the steel GMT-Master II “Pepsi” in 2026? | World | default_keep_uncertain | 1 | $43,041 |
| `KXBLUETSUNAMICOMBO` | Will there be a blue tsunami? | Elections | default_keep_uncertain | 1 | $40,288 |
| `KXCAQLEADER` | Who will win the next CAQ leadership election? | Elections | default_keep_uncertain | 6 | $39,785 |
| `KXGOVILNOMR` | Gov IL Nom R | Elections | default_keep_uncertain | 5 | $38,308 |
| `KXELECTIONBILL` | Will proof of citizenship be required for federal voter registration? | Politics | default_keep_uncertain | 5 | $37,875 |
| `KXSENATEKYR` | KYR | Elections | default_keep_uncertain | 7 | $36,892 |

...and 37 more series with $10K+ notional. See CSV.

### Elections `default_keep_uncertain` (top 30 by notional)

Total series in this set: **209**. With $10K+ aggregate mid-market notional: **40**. Below $10K: **169** (count-only; see CSV for individual rows).

Top 30 by aggregate notional:

| series_ticker | title | Kalshi category | subtag | candidate markets | total mid-market notional |
| --- | --- | --- | --- | --- | --- |
| `CONTROLH` | House winner | Elections | default_keep_uncertain | 2 | $2,949,966 |
| `KXPRESPERSON` | Pres person | Elections | default_keep_uncertain | 25 | $1,386,287 |
| `CONTROLS` | Senate winner | Elections | default_keep_uncertain | 2 | $1,116,374 |
| `KXSENATEMED` | MED | Elections | default_keep_uncertain | 9 | $905,917 |
| `KXTRUMPOUT27` | Trump out as President? | Elections | default_keep_uncertain | 5 | $576,960 |
| `KXHOUSERACE` | House Race Winner? | Elections | default_keep_uncertain | 720 | $343,419 |
| `KXGA14S` | Who will win the GA-14 special election? | Elections | default_keep_uncertain | 29 | $242,311 |
| `KXSENATEILD` | ILD | Elections | default_keep_uncertain | 14 | $201,217 |
| `KXREDISTRICTING` | Redistricting | Elections | default_keep_uncertain | 24 | $195,255 |
| `KXPRESPARTY` | Party winning presidency | Elections | default_keep_uncertain | 2 | $124,481 |
| `KX2028DRUN` | 2028 D running | Elections | default_keep_uncertain | 35 | $106,200 |
| `KXGORDONDENTONBY` | Who will win the 2026 Gordon and Denton by-election? | Elections | default_keep_uncertain | 9 | $71,264 |
| `KXCOSTARICAPRES` | Costa Rica presidency | Elections | default_keep_uncertain | 9 | $63,166 |
| `KXBLUEWAVECOMBO` | Will there be a blue wave? | Elections | default_keep_uncertain | 1 | $57,323 |
| `KX2028RRUN` | 2028 R running | Elections | default_keep_uncertain | 29 | $53,827 |
| `KXCAWEALTHTAX` | Will the California billionaire wealth tax initiative pass? | Elections | default_keep_uncertain | 1 | $53,503 |
| `KXSENATESCR` | SCR | Elections | default_keep_uncertain | 4 | $52,486 |
| `KXTRUMPENDORSE` | who will Trump endorse | Elections | default_keep_uncertain | 14 | $50,256 |
| `KXCITRINI` | Will the Citrini scenario materialize? | Elections | default_keep_uncertain | 1 | $48,867 |
| `KXBADWUR` | Baden-Württemberg Parliamentary Election Winner | Elections | default_keep_uncertain | 3 | $46,734 |
| `KXBLUETSUNAMICOMBO` | Will there be a blue tsunami? | Elections | default_keep_uncertain | 1 | $40,288 |
| `KXCAQLEADER` | Who will win the next CAQ leadership election? | Elections | default_keep_uncertain | 6 | $39,785 |
| `KXGOVILNOMR` | Gov IL Nom R | Elections | default_keep_uncertain | 5 | $38,308 |
| `KXSENATEKYR` | KYR | Elections | default_keep_uncertain | 7 | $36,892 |
| `KXSENATEALR` | ALR | Elections | default_keep_uncertain | 8 | $35,893 |
| `KXSENATEILR` | ILR | Elections | default_keep_uncertain | 5 | $33,765 |
| `KXPRESELECTIONOCCUR` | 2028 election occurring  | Elections | default_keep_uncertain | 1 | $28,961 |
| `KXGOVSDNOMR` | Gov SD Nom r | Elections | default_keep_uncertain | 4 | $23,582 |
| `POWER` | Party power | Elections | default_keep_uncertain | 8 | $23,294 |
| `KXGOVORNOMR` | Gov OR Nom R | Elections | default_keep_uncertain | 8 | $19,459 |

...and 10 more series with $10K+ notional. See CSV.

### Politics `default_keep_uncertain` (top 30 by notional)

Total series in this set: **167**. With $10K+ aggregate mid-market notional: **46**. Below $10K: **121** (count-only; see CSV for individual rows).

Top 30 by aggregate notional:

| series_ticker | title | Kalshi category | subtag | candidate markets | total mid-market notional |
| --- | --- | --- | --- | --- | --- |
| `KXGOVTSHUTLENGTH` | How long will the next government shutdown last? | Politics | default_keep_uncertain | 19 | $3,306,310 |
| `KXTRUMPADMINLEAVE` | Who will leave the Trump administration | Politics | default_keep_uncertain | 35 | $1,246,032 |
| `KXGOLDCARDS` | Gold cards sold | Politics | default_keep_uncertain | 6 | $520,937 |
| `KXNEXTAG` | Next AG | Politics | default_keep_uncertain | 22 | $397,186 |
| `KXARREST` | ARRESTS | Politics | default_keep_uncertain | 24 | $285,281 |
| `KXLAGODAYS` | Mar-a-Lago trips | Politics | default_keep_uncertain | 15 | $195,731 |
| `KXGREENTERRITORY` | Greenland acquisition | Politics | default_keep_uncertain | 3 | $182,368 |
| `KXTRUMPMEET` | Who will Trump meet? | Politics | default_keep_uncertain | 34 | $162,878 |
| `KXGREENLANDPRICE` | How much will Greenland be acquired for? | Politics | default_keep_uncertain | 8 | $137,749 |
| `KXMJSCHEDULE` | MJ schedule | Politics | default_keep_uncertain | 4 | $113,053 |
| `KXMUSKOAI` | Elon Win vs Open AI | Politics | default_keep_uncertain | 1 | $111,780 |
| `KXTRUMPCOUNTRIES` | What countries will Trump visit this year? | Politics | default_keep_uncertain | 24 | $111,571 |
| `KXLEAVESTARMER` | Keir Starmer out? | Politics | default_keep_uncertain | 4 | $108,124 |
| `KXTRUMPMEETING` | Who will Trump meet this year? | Politics | default_keep_uncertain | 20 | $90,547 |
| `KXFEDERALCHARGE` | Who will be charged with a federal crime | Politics | default_keep_uncertain | 33 | $90,079 |
| `KXSUPERBOWLWHITEHOUSE` | WILL THE WINNERS OF THE PRO FOOTBALL CHAMPIONSHIP GO TO THE WHITE HOUSE?  | Politics | default_keep_uncertain | 1 | $74,975 |
| `KXDHSCOMPONENT` | Which DHS components will be funded? | Politics | default_keep_uncertain | 5 | $69,095 |
| `KXDJTWHDINNER` | DJT WH Correspondents dinner | Politics | default_keep_uncertain | 2 | $54,999 |
| `KXTRUMPPARDONS` | Who will Trump pardon? | Politics | default_keep_uncertain | 49 | $54,334 |
| `KXCRYPTOSTRUCTURE` | Crypto market structure | Politics | default_keep_uncertain | 7 | $51,763 |
| `KXTRUMPAPPROVALBELOW` | How low will Trump's approval get this year? | Politics | default_keep_uncertain | 8 | $51,385 |
| `KXTRUMPREMOVE` | Trump removed | Politics | default_keep_uncertain | 1 | $51,116 |
| `KXINSURRECTION` | Insurrection | Politics | default_keep_uncertain | 4 | $48,830 |
| `KXUAPFILES` | UAP files | Politics | default_keep_uncertain | 1 | $46,732 |
| `KXELECTIONBILL` | Will proof of citizenship be required for federal voter registration? | Politics | default_keep_uncertain | 5 | $37,875 |
| `KXSWENCOUNTERS` | SW border encounters | Politics | default_keep_uncertain | 15 | $34,772 |
| `KXNYCRENTFREEZE` | NYC rent freeze | Politics | default_keep_uncertain | 1 | $30,198 |
| `KXTRUMPSTATES` | Which states will Trump visit this year? | Politics | default_keep_uncertain | 16 | $30,166 |
| `KX14AMENDCASE` | Trump birthright citizenship case | Politics | default_keep_uncertain | 1 | $29,208 |
| `KXUSAEXPANDTERRITORY` | Will the US acquire new territory? | Politics | default_keep_uncertain | 5 | $25,915 |

...and 16 more series with $10K+ notional. See CSV.

### World `default_keep_uncertain` (top 30 by notional)

Total series in this set: **2**. With $10K+ aggregate mid-market notional: **1**. Below $10K: **1** (count-only; see CSV for individual rows).

Top 1 by aggregate notional:

| series_ticker | title | Kalshi category | subtag | candidate markets | total mid-market notional |
| --- | --- | --- | --- | --- | --- |
| `KXBEZELD` | Will Rolex discontinue production of the steel GMT-Master II “Pepsi” in 2026? | World | default_keep_uncertain | 1 | $43,041 |

### (null/empty) keyword-classified set (top 30 by notional)

Total series in this set: **0**. With $10K+ aggregate mid-market notional: **0**. Below $10K: **0** (count-only; see CSV for individual rows).

*No series in this set have $10K+ aggregate notional.*

## Out-of-scope universe: recurring-cycle short-duration markets

1. The 30-day duration filter culled **50,618 of 57,252 raw markets (88%)** in this discovery run. A meaningful fraction of these are recurring daily/weekly cycle series (`KXGASD` daily gas, `KXNATGASD` natural gas daily, weekly Hormuz contracts, etc.).

2. These are **not independent markets** — they are sequential contracts on the same underlying time series. The May 1 oil price contract and the May 2 oil price contract are adjacent samples of one continuous price signal, not two independent draws.

3. Stitched together, these series have **potentially unbounded history** available, far beyond what the 30-day filter implies. They are out of scope for THIS discovery (which targeted long-duration markets per the stated category scope), but they represent a substantial future expansion of the effective universe for Test B.

4. **Top 20 recurring-cycle KEPT series by aggregate volume** (sum of `volume_fp` across all sub-30-day contracts in the series, settle/expiration within the last 12 months). Starting point for future stitching analysis:

| rank | series_ticker | title | Kalshi category | label | subtag | short-dur markets (12mo) | total volume_fp (12mo) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `KXWTI` | WTI oil on day | Commodities | macro | hard_include | 4,028 | 34,102,202 |
| 2 | `KXAAAGASW` | US gas price up | Economics | macro | hard_include | 280 | 19,319,217 |
| 3 | `KXWTIW` | WTI oil weekly range | Commodities | macro | hard_include | 480 | 11,684,940 |
| 4 | `KXAAAGASM` | US gas price | Economics | macro | hard_include | 80 | 10,780,421 |
| 5 | `KXGOVTSHUTLENGTH` | How long will the next government shutdown last? | Politics | geopolitics_uncertain | default_keep_uncertain | 7 | 4,704,007 |
| 6 | `KXCLOSEHORMUZ` | Strait of Hormuz | Politics | geopolitics | keyword_geo_foreign_policy | 2 | 4,362,301 |
| 7 | `KXTRUMPPHOTO` | Trump photos | Politics | geopolitics_uncertain | default_keep_uncertain | 3 | 3,773,109 |
| 8 | `KXINX` | S&P 500 range | Financials | macro | hard_include | 1,410 | 3,347,083 |
| 9 | `KXAAAGASD` | US gas price up | Economics | macro | hard_include | 752 | 2,929,241 |
| 10 | `KXBRENTD` | Brent Oil Daily | Commodities | macro | hard_include | 703 | 1,924,525 |
| 11 | `KXINXU` | S&P 500 above/below | Financials | macro | hard_include | 6,080 | 1,292,184 |
| 12 | `KXHORMUZTRAFFICW` | Traffic through the Strait of Homuz? | Elections | geopolitics | keyword_geo_foreign_policy | 61 | 1,285,939 |
| 13 | `KXBRENTW` | Brent Oil | Commodities | macro | hard_include | 150 | 1,281,284 |
| 14 | `KXWTIMAX` | WTI oil yearly high | Commodities | macro | hard_include | 7 | 1,172,039 |
| 15 | `KXGOLDD` | Gold Daily | Commodities | macro | hard_include | 873 | 979,268 |
| 16 | `KXAPRPOTUS` | President RCP approval rating this week | Politics | geopolitics_uncertain | default_keep_uncertain | 80 | 945,857 |
| 17 | `KXNASDAQ100` | Nasdaq range | Financials | macro | hard_include | 1,410 | 894,652 |
| 18 | `KXWTIMINM` | WTI oil monthly low | Commodities | macro | hard_include | 12 | 878,453 |
| 19 | `KXLAGODAYS` | Mar-a-Lago trips | Politics | geopolitics_uncertain | default_keep_uncertain | 7 | 818,371 |
| 20 | `KXU3` | Unemployment | Economics | macro | hard_include | 20 | 782,668 |

Note on volume units: `volume_fp` is a contract-count quantity (per the field-suffix convention in CLAUDE.md). To convert to a dollar notional for ranking purposes, multiply by an average contract price; for ranking *between series* of similar contract types, raw `volume_fp` is sufficient.

## Top candidates per liquidity tier × label

(For sanity-checking the candidate set. Sorted by mid-market notional, descending.)

### Tier <$10K × macro (n=3,087)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXECONSTATCPIYOY-26MAR-T3.3` | CPI year-over-year in Mar 2026? | Economics | $9,883 |
| `KXCBDECISIONEU-26APR30-HOLD` | Will the European Central Bank Maintain current rate at the April ECB Governing Council monetary policy meeting? | Economics | $9,865 |
| `KXGOVMINOMR-26-JJ` | Will John James be the Republican nominee for Governor in Michigan? | Elections | $9,776 |
| `KXCPICOREYOY-26MAR-T2.6` | Will the rate of core CPI inflation be above 2.6% for the year ending in March 2026? | Economics | $9,708 |
| `KXCPICOREYOY-26APR-T2.6` | Will the rate of core CPI inflation be above 2.6% for the year ending in April 2026? | Economics | $9,635 |
| `KXPAYROLLS-26FEB-T90000` | Will above 90000 jobs be added in February 2026? | Economics | $9,486 |
| `SENATEMI-26-R` | Will Republicans win the Senate race in Michigan? | Elections | $9,478 |
| `KXSOLAR-25-30` | Will at least 30 GWdc of solar capacity be installed in 2025? | Economics | $9,467 |
| `KXDEBTGROWTH-28DEC31-40` | Will the national debt hit $40 trillion during the Trump Administration? | Economics | $9,463 |
| `KXPCECORE-26JAN-T0.2` | Will the rate of core PCE inflation be above 0.2% in January 2026? | Economics | $9,443 |

### Tier <$10K × geopolitics (n=730)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXVISITVENEZUELA-26MAY01-CMYE` | Will Charles Myers visit Venezuela before May 1, 2026? | Politics | $9,732 |
| `KXABRAHAMSA-29-JAN20` | Will Israel and Saudi Arabia normalize relations before Jan 20, 2029? | Politics | $8,908 |
| `KXTAIWANLVL4-27JAN01` | Will the U.S. State Department issue a Level 4 warning for Taiwan before Jan 1, 2027? | Politics | $8,861 |
| `KXUSAIRANAGREEMENT-27-26APR` | Will the US agree to a new Iranian nuclear deal before April? | Politics | $8,626 |
| `KXCOLOMBIAPARLI-26MAR08-HPAC` | Will Historic Pact win the 2026 Colombian Chamber of Representatives election? | Elections | $8,237 |
| `KXVENEZUELALEADER2-26JUN01-DROD` | Will Delcy Rodríguez be the head of state of Venezuela on Jun 1, 2026? | Elections | $7,897 |
| `KXIRANDEMOCRACY-27MAR01-T6` | Will Iran's score in the Economist Intelligence Unit's Democracy Index be at least 6 in the 2026 edition? | Elections | $7,030 |
| `KXUSAIRANAGREEMENT-27-29JAN20` | Will the US agree to a new Iranian nuclear deal this year? | Politics | $6,969 |
| `KXDENMARKPM-26MAR24-MFRE` | Will Mette Frederiksen become Prime Minister of Denmark following the 2026 Danish general election? | Elections | $6,460 |
| `KXLOSEREELECTIONDSEN-2026-0` | Will exactly 0 Senate Democrats lose re-election in 2026? | Elections | $6,366 |

### Tier <$10K × geopolitics_uncertain (n=2,343)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXGOVIANOMR-26-RFEE` | Will Randy Feenstra be the Republican nominee for Governor in Iowa? | Elections | $9,936 |
| `KXHOUSERACE-TX13-26-R` | Will Republican win the House race for TX-13? | Elections | $9,807 |
| `KXTRUMPCOUNTRIES-27JAN01-TUR` | Will Donald Trump visit Turkey before Jan 1, 2027? | Politics | $9,798 |
| `KXPRESPERSON-28-SSMI` | Who will win the next presidential election? | Elections | $9,760 |
| `KXHOUSERACE-CA15-26-D` | Will Democratic win the House race for CA-15? | Elections | $9,648 |
| `KX2028DRUN-28-WMOO` | Who will run for the Democratic presidential nomination in 2028? | Elections | $9,547 |
| `KX2028RRUN-28-JVAN` | Who will run for the Republican presidential nomination in 2028? | Elections | $9,502 |
| `KXNEXTAG-29-HDHI` | Who will be Trump's next Attorney General? | Politics | $9,337 |
| `KXSCOTPARLIAMENT-26MAY07-SNP` | Will the SNP win the 2026 Scottish parliament election? | Elections | $9,120 |
| `KXUSAEXPANDTERRITORY-28JAN01` | Will the United States acquire any territory not under its sovereignty (as of Issuance) before Jan 1, 2028? | Politics | $9,030 |

### Tier $10K-$25K × macro (n=80)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXCPI-26FEB-T0.0` | Will CPI rise more than 0.0% in February 2026? | Economics | $24,905 |
| `KXCPI-26APR-T0.3` | Will CPI rise more than 0.3% in April 2026? | Economics | $24,806 |
| `KXNASDAQ100POS-26DEC31H1600-T25249.85` | Will the Nasdaq-100 be above 25249.85 at the end of Dec 31, 2026 at 4pm EST? | Financials | $24,753 |
| `KXINXMAXY-01JAN2027-7599.99` | Will the maximum SP500 value reach 7599.99 by Jan 1, 2027? | Financials | $24,288 |
| `KXCBDECISIONCANADA-26MAR-H0` | Will Bank of Canada Hike rates by 0bps at their March 2026 meeting? | Economics | $24,246 |
| `KXBTCVSGOLD-26` | Will Bitcoin outperform gold in 2026? | Financials | $23,791 |
| `KXPAYROLLS-26FEB-T40000` | Will above 40000 jobs be added in February 2026? | Economics | $23,665 |
| `KXRATECUTCOUNT-26DEC31-T2` | Will the Fed cut rates 2 times? | Economics | $23,478 |
| `KXFEDLEADJUNE-26JUN17-KWAR` | Will Kevin Warsh be Chair of the Federal Open Market Committee (including in acting capacity) on Jun 17, 2026? | Elections | $22,384 |
| `KXPAYROLLS-26FEB-T30000` | Will above 30000 jobs be added in February 2026? | Economics | $22,379 |

### Tier $10K-$25K × geopolitics (n=20)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXBRBALLOT-26-LULA` | Will Luiz Inácio Lula da Silva be on the ballot in the next Brazilian presidential election? | Elections | $24,456 |
| `KXHORMUZNORM-26MAR17-B260515` | Will the 7-day moving average of transit calls through the Strait of Hormuz as reported by the IMF PortWatch be above 60 before May 15, 2026? | Politics | $23,937 |
| `KXHORMUZNORM-26MAR17-B270401` | Will the 7-day moving average of transit calls through the Strait of Hormuz as reported by the IMF PortWatch be above 60 before April 1, 2027? | Politics | $23,534 |
| `KXCOLOMBIAPRESR1-26MAY31-ICAS` | Will Iván Cepeda Castro win the first round of the 2026 Colombian presidential election? | Elections | $21,676 |
| `KXHORMUZNORM-26MAR17-B261001` | Will the 7-day moving average of transit calls through the Strait of Hormuz as reported by the IMF PortWatch be above 60 before October 1, 2026? | Politics | $20,308 |
| `KXJAPANHOUSE-28-LDP` | Who will win the next Japanese general election? | Elections | $20,149 |
| `KXBRPRES-26-LULA` | Will Luiz Inácio Lula da Silva win the 2026 Brazilian presidential election? | Elections | $19,809 |
| `KXUSAIRANAGREEMENT-27-26JUL` | Will the US agree to a new Iranian nuclear deal this year? | Politics | $18,394 |
| `KXCOLOMBIAPRES-26-ICAS` | Will Iván Cepeda Castro win the next Colombian presidential election? | Elections | $18,130 |
| `KXDENMARKPARLI-26-SD` | Who will win the next Danish general election? | Elections | $16,504 |

### Tier $10K-$25K × geopolitics_uncertain (n=57)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXTRUMPMEETING-27JAN01-NJROG` | Will Donald Trump meet in person Joe Rogan before Jan 1, 2027? | Politics | $24,888 |
| `KXINSURRECTION-29-27` | Will Trump invoke the Insurrection Act during his Presidency? | Politics | $23,847 |
| `KXTRUMPENDORSE-26SEP15-ABAR` | Will Donald Trump endorse Andy Barr in the 2026 Kentucky Senate Republican primary before May 19, 2026? | Elections | $23,770 |
| `KXPRESPERSON-28-MKEL` | Who will win the next presidential election? | Elections | $23,307 |
| `KXINSURRECTION-29` | Will Trump invoke the Insurrection Act during his Presidency? | Politics | $23,289 |
| `KXPRESPERSON-28-TCAR` | Who will win the next presidential election? | Elections | $22,536 |
| `KXTRUMPMEETING-27JAN01-NZMAM` | Will Donald Trump meet in person Zohran Mamdani before Jan 1, 2027? | Politics | $22,141 |
| `KXTRUMPENDORSE-26SEP15-JLET` | Will Donald Trump endorse Julia Letlow in the 2026 Louisiana Senate Republican primary before May 16, 2026? | Elections | $22,063 |
| `KXWAINCOMETAX-26APR01` | Will a state personal income tax become law in Washington before Apr 1, 2026? | Politics | $19,795 |
| `KXPRESPERSON-28-RDES` | Who will win the next presidential election? | Elections | $19,747 |

### Tier $25K-$50K × macro (n=57)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXLCPIMAXYOY-27-P4` | Inflation surge in 2026? | Economics | $48,375 |
| `KXCPI-26MAR-T0.5` | Will CPI rise more than 0.5% in March 2026? | Economics | $47,752 |
| `KXCPIYOY-26APR-T3.6` | Will the rate of CPI inflation be above 3.6% for the year ending in April 2026? | Economics | $47,659 |
| `KXFEDDECISION-26APR-C25` | Will the Federal Reserve Cut rates by 25bps at their April 2026 meeting? | Economics | $47,247 |
| `KXFEDCHAIRNOM-29-CWAL` | Will Trump next nominate Christopher Waller as Fed Chair? | Elections | $46,646 |
| `KXAAAGASMAXFL-26DEC31-3.00` | Will average **gas prices** be above or below $3.00 by Dec 31, 2026? | Economics | $46,182 |
| `KXAAAGASM-26MAR31-3.60` | Will average **gas prices** be above $3.60? | Economics | $45,922 |
| `KXCPI-26APR-T0.4` | Will CPI rise more than 0.4% in April 2026? | Economics | $45,889 |
| `KXAAAGASM-26MAR31-3.30` | Will average **gas prices** be above $3.30? | Economics | $45,255 |
| `KXPAYROLLS-26MAR-T30000` | Will above 30000 jobs be added in March 2026? | Economics | $45,095 |

### Tier $25K-$50K × geopolitics (n=11)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXUSAIRANAGREEMENT-27-26AUG` | Will the US agree to a new Iranian nuclear deal before August? | Politics | $47,165 |
| `KXTARIFFCHECKS-26-27` | Will it be reported that at least one million Americans have received checks of at least $1000 directly attributable to tariff revenue? | Politics | $39,667 |
| `KXPERUPRES2ND-26MAR25-2-RPAL` | Will Roberto Sánchez Palomino finish 2nd in the first round of the 2026 Peruvian presidential election? | Elections | $38,381 |
| `KXPERUPRES1R-26APR12-KFUJ` | Will Keiko Fujimori win the first round of the 2026 Peruvian presidential election? | Elections | $35,834 |
| `KXTRUMPCHINA-26-JUN01` | Will Donald Trump visit China before Jun 1, 2026? | Politics | $34,928 |
| `KXPERUPRESMATCHUP-26APR-KFUJRPAL` | Will Keiko Fujimori and Roberto Sánchez Palomino be the nominees in the 2026 Peru presidential election? | Elections | $32,195 |
| `KXHORMUZNORM-26MAR17-B260801` | Will the 7-day moving average of transit calls through the Strait of Hormuz as reported by the IMF PortWatch be above 60 before August 1, 2026? | Politics | $29,860 |
| `KXCOLOMBIAPRES-26-AESP` | Will Abelardo de la Espriella win the next Colombian presidential election? | Elections | $29,048 |
| `KXTHAILANDHOUSE-26FEB08-BJT` | Will Bhumjaithai Party win the 2026 Thailand House of Representatives election? | Elections | $28,888 |
| `KXUSAIRANAGREEMENT-27-26JUN` | Will the US agree to a new Iranian nuclear deal this year? | Politics | $27,783 |

### Tier $25K-$50K × geopolitics_uncertain (n=31)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXCITRINI-28JUL01` | Will the number of unemployment rate exceeds 10% (monthly BLS); S&P 500 declines more than 30% from its closing level on Issuance; Zillow Home Value Index declines more than 10% YoY in any of: NYC, LA, San Francisco, Chicago, Houston, Phoenix; labor share of gross domestic income (GDI) first-release value for any quarter falls below 50%; CPI-U (All items, not seasonally-adjusted) YoY falls below 0% in any monthly release during before July 2028 be above 2? | Elections | $48,867 |
| `KXSENATESCR-26-LGRA` | Will Lindsey Graham be the Republican nominee for the Senate in South Carolina? | Elections | $48,501 |
| `KXPRESPARTY-2028-R` | Will Republican win the Presidency in 2028? | Elections | $47,593 |
| `KXUAPFILES-27` | Will Trump release new UFO files before 2027? | Politics | $46,732 |
| `KXTRUMPADMINLEAVE-26DEC31-KLEA` | Will Karoline Leavitt leaves White House Press Secretary in before 2027? | Politics | $46,186 |
| `KXBADWUR-26MAR08-GRE` | Will the Greens win the 2026 Baden-Württemberg state election? | Elections | $45,471 |
| `KXPRESPERSON-28-ABES` | Who will win the next presidential election? | Elections | $44,763 |
| `KXBEZELD-26` | Will Rolex discontinue the production of the steel GMT-Master II “Pepsi” in 2026? | World | $43,041 |
| `KXPRESPERSON-28-DTRU` | Who will win the next presidential election? | Elections | $42,547 |
| `KXBLUETSUNAMICOMBO-27FEB` | Will Democrats hold 235 or more seats in the House after the 2026 midterms AND hold 51 or more seats in the Senate after the 2026 midterms? | Elections | $40,288 |

### Tier $50K-$100K × macro (n=34)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXNFLXINCREASE-26` | Netflix Streaming Subscription price increase in 2026? | Financials | $97,583 |
| `KXCPIYOY-26MAR-T3.2` | Will the rate of CPI inflation be above 3.2% for the year ending in March 2026? | Economics | $97,191 |
| `KXFED-26APR-T3.50` | Will the upper bound of the federal funds rate be above 3.50% following the Fed's Apr 29, 2026 meeting? | Economics | $96,726 |
| `KXCPI-26MAR-T0.7` | Will CPI rise more than 0.7% in March 2026? | Economics | $96,527 |
| `KXWTIMAX-26DEC31-T120` | Will the maximum WTI front month settle price reach $120.01 by Dec 31, 2026? | Commodities | $95,109 |
| `KXCPI-26MAR-T0.6` | Will CPI rise more than 0.6% in March 2026? | Economics | $93,074 |
| `KXCPIYOY-26MAR-T3.0` | Will the rate of CPI inflation be above 3.0% for the year ending in March 2026? | Economics | $86,582 |
| `KXGDP-26APR30-T1.0` | Will **real GDP** increase by more than 1.0% in Q1 2026? | Economics | $82,499 |
| `KXGDP-26APR30-T2.0` | Will **real GDP** increase by more than 2.0% in Q1 2026? | Economics | $80,137 |
| `KXEMERCUTS-26-T0` | Will the Fed cut rates 0 times at emergency meetings? | Economics | $79,337 |

### Tier $50K-$100K × geopolitics (n=12)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXTRUMPCHINA-26-MAY15` | Will Donald Trump visit China before May 15, 2026? | Politics | $94,762 |
| `KXPERUPRES-26-RPAL` | Will Roberto Sánchez Palomino win the next Peruvian presidential election? | Elections | $89,883 |
| `KXTARIFFREFUND-25-26JUN30` | Will a court order the Trump Administration to give tariff refunds this year? | Politics | $83,428 |
| `KXHORMUZNORM-26MAR17-B270101` | Will the 7-day moving average of transit calls through the Strait of Hormuz as reported by the IMF PortWatch be above 60 before January 1, 2027? | Politics | $73,170 |
| `KXPAHLAVIVISITA-27JAN01` | Will Reza Pahlavi visit Iran before Jan 1, 2027? | Politics | $72,947 |
| `KXHUNGARYPARLI-26-OPP` | Who will win the next Hungarian general election? | Elections | $71,310 |
| `KXHORMUZNORM-26MAR17-B260601` | Will the 7-day moving average of transit calls through the Strait of Hormuz as reported by the IMF PortWatch be above 60 before June 1, 2026? | Politics | $70,636 |
| `KXBRPRES-26-FBOL` | Will Flávio Bolsonaro win the 2026 Brazilian presidential election? | Elections | $70,342 |
| `KXTARIFFREFUND-25-26DEC` | Will a court order the Trump Administration to give tariff refunds this year? | Politics | $59,732 |
| `KXHORMUZNORM-26MAR17-B270701` | Will the 7-day moving average of transit calls through the Strait of Hormuz as reported by the IMF PortWatch be above 60 before July 1, 2027? | Politics | $55,184 |

### Tier $50K-$100K × geopolitics_uncertain (n=20)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXTRUMPADMINLEAVE-26DEC31-TGAB` | Will Tulsi Gabbard leaves Director of National Intelligence in before 2027? | Politics | $96,341 |
| `KXTRUMPOUT27-27-26AUG01` | Will Donald Trump leave office before August 1, 2026? | Elections | $96,310 |
| `KXTRUMPADMINLEAVE-26DEC31-LCHA` | Will Lori Chavez-DeRemer leaves Secretary of Labor in before 2027? | Politics | $88,374 |
| `KXREDISTRICTING-26-VIR` | What states will redistrict before the 2026 Congressional elections? | Elections | $82,178 |
| `KXTRUMPOUT27-27-JAN2029` | Will Donald Trump leave office before January 20, 2029? | Elections | $81,951 |
| `KXPRESPERSON-28-AOCA` | Who will win the next presidential election? | Elections | $78,635 |
| `KXPRESPARTY-2028-D` | Will Democratic win the Presidency in 2028? | Elections | $76,888 |
| `KXSUPERBOWLWHITEHOUSE-26DEC31` | Will The winning team of the 2026 Pro football Championship visit The White House before Dec 31, 2026? | Politics | $74,975 |
| `KXPRESPERSON-28-KHAR` | Who will win the next presidential election? | Elections | $71,101 |
| `KXGORDONDENTONBY-26MAR01-HSPE` | Will Hannah Spencer win the the 2026 Gordon and Denton by-election? | Elections | $70,693 |

### Tier $100K+ × macro (n=25)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXFEDCHAIRNOM-29-KW` | Will Trump next nominate Kevin Warsh as Fed Chair? | Elections | $15,969,993 |
| `KXFEDDECISION-26MAR-H0` | Will the Federal Reserve Hike rates by 0bps at their March 2026 meeting? | Economics | $8,751,998 |
| `KXFEDDECISION-26APR-H0` | Will the Federal Reserve Hike rates by 0bps at their April 2026 meeting? | Economics | $5,553,784 |
| `KXFEDDECISION-26JUN-H0` | Will the Federal Reserve Hike rates by 0bps at their June 2026 meeting? | Economics | $811,233 |
| `KXFEDCHAIRNOM-29-JS` | Will Trump next nominate Judy Shelton as Fed Chair? | Elections | $697,044 |
| `KXFEDCHAIRCONFIRM-KWAR` | Will Kevin Warsh be confirmed as chair of the Board of Governors of the Federal Reserve System before Jan 1, 2029? | Politics | $687,731 |
| `KXFED-26APR-T3.00` | Will the upper bound of the federal funds rate be above 3.00% following the Fed's Apr 29, 2026 meeting? | Economics | $333,648 |
| `KXFEDCHAIRCONFIRMED-26MAY15` | Will Trump’s first officially announced pick for Chairman of the Federal Reserve be confirmed as Chairman of the Federal Reserve before May 15, 2026? | Politics | $267,720 |
| `KXINXPOS-26DEC31H1900-T6845.5` | Will the S&P 500 be above 6845.5 on Dec 31, 2026 at 4pm EST? | Financials | $225,485 |
| `KXFED-26MAR-T2.75` | Will the upper bound of the federal funds rate be above 2.75% following the Fed's Mar 18, 2026 meeting? | Economics | $216,337 |

### Tier $100K+ × geopolitics (n=9)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `KXNEXTIRANLEADER-45JAN01-MKHA` | Will Mojtaba Khamenei be the next Supreme Leader of Iran? | Elections | $602,166 |
| `KXCLOSEHORMUZ-27JAN` | Will Iran close Strait of Hormuz before Jan 2027? | Politics | $465,463 |
| `KXNEXTHUNGARYPM-26MAY01-PMAG` | Will Péter Magyar become Prime Minister of Hungary following the 2026 Hungarian parliamentary election? | Elections | $416,309 |
| `KXPERUPRES-26-KFUJ` | Who will win the next Peruvian presidential election? | Elections | $152,770 |
| `KXHORMUZNORM-26MAR17-B260701` | Will the 7-day moving average of transit calls through the Strait of Hormuz as reported by the IMF PortWatch be above 60 before July 1, 2026? | Politics | $152,749 |
| `KXHUNGARYPARLIPARTY-26APR12-TISZ` | Will TISZA win the 2026 Hungary parliamentary election? | Elections | $145,552 |
| `KXUSAIRANAGREEMENT-27` | Will the US agree to a new Iranian nuclear deal this year? | Politics | $111,518 |
| `KXVENEZUELALEADER-26DEC31-NMAD` | Will Nicolás Maduro be the head of state of Venezuela on Dec 31, 2026? | Elections | $110,832 |
| `KXVENEZUELALEADER2-26JUN01-NMAD` | Will Nicolás Maduro be the head of state of Venezuela on Jun 1, 2026? | Elections | $107,706 |

### Tier $100K+ × geopolitics_uncertain (n=34)

| ticker | title | Kalshi category | mid-market notional |
| --- | --- | --- | --- |
| `CONTROLH-2026-D` | Will Democrats win the House in 2026? | Elections | $2,296,585 |
| `KXSENATEMED-26-GRA` | Will Graham Platner be the Democratic nominee for the Senate in Maine? | Elections | $902,425 |
| `CONTROLH-2026-R` | Will Republicans win the House in 2026? | Elections | $653,381 |
| `CONTROLS-2026-R` | Will Republicans win the U.S. Senate in 2026? | Elections | $589,845 |
| `KXGOVTSHUTLENGTH-26FEB07-G60` | Will the US government be shut down for at least 60 days between Feb 7, 2026 and Dec 31, 2026? | Politics | $576,078 |
| `CONTROLS-2026-D` | Will Democrats win the U.S. Senate in 2026? | Elections | $526,529 |
| `KXGOLDCARDS-26-B0.0` | How many Gold Cards will Trump sell this year? | Politics | $482,430 |
| `KXGOVTSHUTLENGTH-26FEB07-G70` | Will the US government be shut down for at least 70 days between Feb 7, 2026 and Dec 31, 2026? | Politics | $409,615 |
| `KXGOVTSHUTLENGTH-26FEB07-G50` | Will the US government be shut down for at least 50 days between Feb 7, 2026 and Dec 31, 2026? | Politics | $401,323 |
| `KXGOVTSHUTLENGTH-26FEB07-G43` | Will the US government be shut down for at least 43 days between Feb 7, 2026 and Dec 31, 2026? | Politics | $376,367 |
