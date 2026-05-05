# Investment thesis — Kalshi favorite-longshot bias strategy (v2.1)

**Author:** Peter Yakovlev
**Date written:** May 5, 2026
**Version:** 2.1
**Version history:**
- v1: YES-bias frame (deprecated)
- v2: Full rewrite around favorite-longshot bias after first-round Gemini adversarial review surfaced the Bürgi-Deng-Whelan (2026) CEPR paper
- v2.1 (this version): Targeted edits after second-round Gemini review surfaced toxic-fill / adverse-selection risk and refined the Phase 2 framing
**Status:** Pre-test. Written before any backtest is run. Will be committed to git as `notes/investment-thesis.md` before any test code exists.

---

## Why this document exists

The discipline this document enforces, taken from my father's letter to my brother and from López de Prado's framing of pre-registered hypotheses: **a backtest without a thesis is data mining, and the thesis must be written before the test result can color it.** This document states, in advance, what is being tested, why it should work, what would prove it wrong, and what would convince me I have something real. Test B's results are interpreted against this document, not against retroactive rationalization.

## Why v2 exists (the v1 → v2 pivot)

V1 framed the strategy as "YES-bias" — retail overpaying for YES contracts. Adversarial review revealed two problems that forced a rewrite:

1. **An internal contradiction.** V1 claimed retail "over-weights risk on dramatic YES outcomes" and simultaneously claimed "low-volatility markets under-weight tail risk." Both can't be sources of edge; they imply opposite mispricings.

2. **A documented academic finding I didn't know about.** Bürgi, Deng, and Whelan (2026), published as CEPR Discussion Paper 20631 and UCD Working Paper 2025/19, analyzed 313,972 prices on 46,282 Kalshi contracts and found that the actual mispricing pattern is **favorite-longshot bias**, not YES-bias: low-priced contracts win less often than their price implies, high-priced contracts win more often than their price implies. The bias is symmetric around contract price, not around YES/NO label. This is the well-known pattern from horse racing and sports betting, replicated on Kalshi.

V2 is grounded in this paper rather than in invented behavioral framing. The test is a replication-and-extension of established findings, applied to a specific market subset.

---

## 1. The hypothesis (one sentence)

**I believe that the favorite-longshot bias documented by Bürgi-Deng-Whelan (2026) on the broad Kalshi universe persists in the long-dated, low-volatility macro and geopolitics sub-universe, and that a maker-side strategy systematically selling overpriced longshots and buying underpriced favorites within this sub-universe earns positive risk-adjusted returns net of fees and bid-ask spreads.**

This is a falsifiable claim with three independent failure modes (the bias may not exist in our sub-universe, the sub-universe may not have enough liquidity, or fees and spreads may eat the edge). Each failure mode produces a different conclusion if Test B fails — useful diagnostic information either way.

## 2. The source of edge

The edge is **the favorite-longshot bias**: a documented systematic mispricing where low-probability outcomes are over-priced and high-probability outcomes are under-priced.

The mechanism, per the CEPR paper's theoretical model:

- **Makers** (limit-order posters) seek positive expected returns net of fees, but are slightly over-optimistic about win probabilities — they post offers that look profitable to themselves but slightly under-state actual win rates. This is the "winner's curse" pattern: the most aggressive offers get filled, and the most aggressive offers tend to be over-confident.
- **Takers** (market-order executors) accept these offers because they are impatient or believe they have an information advantage. Empirically, they don't — takers earn -32% post-fee returns on average across the CEPR sample.
- **Fees fall harder on cheap contracts** because they are a per-contract charge regardless of contract price. A 0.7¢ fee on a 5¢ contract is 14% of the price; the same fee on a 90¢ contract is 0.8%. This compounds the favorite-longshot pattern.

This is **structural and behavioral combined**, not purely behavioral. The structure (per-contract fees, maker/taker microstructure) makes the behavioral bias (over-optimism by makers) more profitable for the disciplined side and more punishing for the undisciplined side.

## 3. Why the bias might persist on Kalshi specifically

Three reasons the bias hasn't been arbitraged away:

1. **Institutional saturation is partial, not complete.** Susquehanna and a few other domestic firms operate as market makers on Kalshi. They reduce the magnitude of the bias but do not eliminate it — the CEPR paper documents the bias persisting across the full 2021–2025 sample even with Kalshi's growing institutional flow.

2. **Cross-market arbitrage is constrained.** Unlike crypto or equities where international quant capital flows freely, Kalshi's CFTC-regulated US-only structure prevents the global arbitrage that flattens biases in other markets. Polymarket is the natural competitor but operates under a different regulatory regime, attracting different flow.

3. **The bias is decaying, but slowly.** The CEPR paper notes "some evidence that the bias in prices is getting smaller over time" — this is a known risk. Test B must use recent data to reflect current platform behavior, not legacy 2021–2022 inefficiencies that no longer exist.

## 4. Why long-dated, low-volatility, macro/geopolitics specifically

The CEPR paper documents the bias on the *full* Kalshi universe. The question for this thesis is whether the bias is **stronger, weaker, or the same** in our specific sub-universe.

Hypotheses for each:

- **Long-dated markets** (≥30 days to resolution at open): more time for behavioral mispricing to embed before resolution forces price convergence. Hypothesis: bias is stronger here than on short-duration markets where resolution proximity disciplines prices. The CEPR paper excludes hourly-reset markets but doesn't separately analyze long-dated vs. short-dated within the daily+ sample, leaving this as a real research question.

- **Low-volatility markets** (those that have not moved >X% in window W): retail attention is lower on quiet markets, and the makers who would normally arbitrage the bias may be less attentive. Hypothesis: bias is stronger here. This is testable as a sub-cut.

- **Macro and geopolitics specifically**: the universe-discovery session showed these categories have ~$10K–$100K liquidity tier with reasonable depth. They also have clearer news drivers than entertainment or sports markets, making the favorite-longshot framing more applicable (the "favorite" is the consensus outcome; the "longshot" is the contrarian one). Hypothesis: bias is comparable to the Kalshi-wide average, not stronger or weaker.

The actual answers are what Test B determines.

## 5. Why maker-side execution is the only viable strategy

This was the most important finding from the CEPR paper. Reproducing the relevant numbers:

| Metric | Takers (market orders) | Makers (limit orders) |
|---|---|---|
| Average return, post-fees | -32% | -10% |
| Net returns on cheap contracts (1¢-10¢) | Heavy losses | Moderate losses |
| Net returns on expensive contracts (90¢-99¢) | Roughly break-even | **Small positive returns** |

Makers selling longshots and buying favorites is the only side of this market where positive returns appear empirically. The CEPR paper's theoretical model explains this through three channels:
- Makers capture the bid-ask spread (rather than paying it like Takers do)
- Maker fees are 25% of taker fees per Kalshi's fee schedule
- Maker selection (you only get filled when someone disagrees with you) is roughly neutral for high-priced favorites and slightly adverse for low-priced longshots — but the magnitude of the bias on longshots is large enough to overcome the adverse selection.

**Strategic implication:** the strategy must be maker-side. Taker-side execution is dominated by every fee/spread/selection factor combined.

This is a substantive change from v1, which proposed maker-side as a Phase 1b experiment. V2 makes maker-side the core architecture from Phase 1a.

## 6. Success target — derived from real numbers, not gut feel

Kalshi's actual fee schedule (verified May 2026):
- **Taker fee** = 7% × P × (1−P) per contract
- **Maker fee** = 1.75% × P × (1−P) per contract (25% of taker)

Worked maker-fee examples:
- Contract at $0.10 → maker fee 0.16¢/contract round-trip 0.31¢ (~3.1% of price)
- Contract at $0.50 → maker fee 0.44¢/contract round-trip 0.88¢ (~1.75% of price)
- Contract at $0.90 → maker fee 0.16¢/contract round-trip 0.31¢ (~0.35% of price)

Treasury bill yield (mid-2026 reference rate, to be locked in Session B): assumed ~4% annualized for the comparison baseline.

**Success criterion (locked, not adjustable post-test):**

> Test B passes if the maker-side strategy earns annualized return ≥ T-bill rate + 3 percentage points, after realistic fees and observed bid-ask spread costs, with statistical significance at p<0.05.

> Test B is *marginal* if it earns 0–3pp above T-bill (real but not large enough to justify operational complexity).

> Test B *fails* if it does not beat T-bill on a risk-adjusted basis, or if the strategy's positive returns are statistically indistinguishable from random selection in the same universe.

The 3pp threshold is set above the maker-fee floor and the typical bid-ask spread floor by a margin large enough to provide signal-to-noise separation given expected sample size (~500-1500 settled contracts in the candidate universe over 6-12 months). Final sample-size-driven threshold to be confirmed in Session B.

## 7. Capacity question — small and explicit

Capacity is severely constrained, and acknowledging this honestly is a strength, not a weakness:

- **Most candidate markets have <$25K open interest.** A $5K position is 20% of the market — large enough that the trader's own actions move the price and erase the edge.
- **Realistic per-position size: $500–$2,000.** Below 10% of average market depth.
- **Realistic total active capital: $10–30K.** Beyond this, the strategy starts moving its own prices.
- **Implication:** this is a small-account strategy. It works because it's small. Scaling it 10x by adding more markets fails because the pool of high-quality candidates is already small. Scaling it 10x by increasing per-position size fails because of market impact.

This is a real constraint, not a footnote. If Test B passes, the strategy is validated for retail-scale operation only. The conversation about whether to scale via agents (the "wall street pit" vision) is a separate, later conversation that depends on whether the bundle pattern from this single bundle generalizes.

## 8. Stop-rule — explicit and binding

I commit, in advance, to abandoning this thesis if any of the following are observed:

- **Test B fails.** Strategy does not beat the T-bill + 3pp threshold with p<0.05. → Bias either doesn't exist in our sub-universe or has been arbitraged away.
- **Test B passes on the 6-12 month sample but the most-recent 3 months show no bias.** → Bias is decaying in real time and has no future even if it has a past. Walk away, do not extrapolate.
- **Maker fill rate is below 30%.** → Even if the model passes on filled trades, it can't deploy capital at scale because most orders never execute.
- **Realistic spread analysis shows average spreads on the candidate universe exceed 4%.** → Bias is real but not exploitable; the spread floor eats the edge regardless of strategy quality.
- **Adverse selection on fills exceeds threshold (toxic-flow kill condition).** Measure the average price movement in the 30 minutes following each filled order. If filled orders are systematically followed by adverse moves above a Session-B-determined threshold (working assumption: >50bps average post-fill move against the position), then the strategy is providing cheap insurance to informed traders rather than capturing the bias. → Walk away; the maker-side architecture is being exploited by faster information flow.
- **Six months of paper-shadow trading post-Test-B shows materially worse performance than backtest.** → Backtest captured a feature the live market doesn't deliver. Walk away.

I commit not to tweak the strategy parameters mid-test to get a passing result. If Test B fails as designed, the thesis fails, period. Re-running with different parameters until it passes is data mining.

## 9. Investor archetype

This is a **systematic short-term maker-side trader** strategy:
- Entries: limit-order posts on candidate markets when the bid-ask cross satisfies pre-registered conditions.
- Holding period: **from fill to settlement only.** No early exits in Phase 1. This is a deliberate constraint — early exits require crossing the spread again, paying a second fee, and modeling exit-slippage dynamics that v2.1 does not address. Hold-to-settlement keeps the test design clean and avoids confounding the bias-capture result with exit-execution skill. Early-exit dynamics become a Phase 2 design question if Phase 1 succeeds.
- Position sizing: small, constrained by per-trade and total-capital caps.
- Risk control: per-trade size cap, total active capital cap, maximum spread filter, minimum-liquidity filter, stale-order cancellation rule (limit orders that have rested for >X hours without filling are auto-cancelled to cap toxic-flow exposure; X to be locked in Session B).

This is *not* a discretionary trader, *not* a market-maker in the institutional sense (no obligation to quote), *not* a fundamental analyst. It is a systematic exploit of a documented behavioral-and-structural mispricing, deployed at retail scale, hold-to-settlement only.

---

## What is explicitly NOT being tested in Phase 1

To prevent scope creep — and because clarity about what's *out* of scope is as load-bearing as what's in — these are deferred:

- **Short-duration recurring commodity markets** (daily oil, daily gold, daily natgas, weekly Hormuz, etc.). **This is the planned Phase 2 research direction** — see dedicated section below.
- **Cycle-trading thesis** (quiet → swing → quiet). Deferred indefinitely. May or may not be tested as a separate phase if Phase 1 or Phase 2 produces an unexpected finding that motivates it.
- **AI-agent system.** The thesis is being tested at its simplest form first. Agents are a future scaling layer and depend on a real edge existing in the first place.
- **Multi-bundle universe** (currencies, equities, commodities cross-platform, crypto). The "wall street pit" architecture is the long-term vision but cannot be designed in advance — it has to emerge from doing one bundle deeply.
- **Live deployment.** No real money during Test B. Paper-shadow only after backtest passes. Real money after at least 90 days of clean paper-shadow performance.
- **Taker-side strategies.** Per Section 5, taker-side execution is dominated. Not being tested.

---

## Phase 2 (planned, conditional on Phase 1 success): Short-duration recurring commodity markets

The CEPR paper explicitly excludes hourly-reset markets from its sample and does not separately analyze daily-reset commodity markets. This is a real research gap.

**Phase 2 hypothesis (to be formalized later):** the favorite-longshot bias documented by CEPR on the broader Kalshi universe is *also present* in short-duration recurring commodity markets (KXOIL, KXGOLD, KXNATGAS, KXBRENT, weekly Hormuz, etc.), and may be *stronger* due to (a) less institutional attention, (b) fragmented liquidity across many short-lived contracts in the same series, and (c) anchor-pricing dynamics where retail traders react to obvious recent moves rather than fundamental probability.

**Why this might be interesting:** Phase 2 would be **original research**, not replication. If the bias is present, it has not been previously documented, and the strategy could be among the first systematic exploitations of it. The 24-hour holding period also gives Phase 2 substantially higher cycle frequency than Phase 1, which compounds statistical power and capital efficiency *if* maker fill rate is acceptable.

**Why this might fail (the spot-discipline counter-argument):** Phase 2's commodity markets are anchored to liquid, well-arbed external spot markets. Favorite-longshot bias in sports markets exists in part because there is no "spot price for a horse" — no external mechanism disciplines the prediction-market price. In commodity markets, the underlying spot price exerts a "gravitational pull" on the Kalshi market. Sophisticated participants on Kalshi can hedge their Kalshi positions in the underlying spot market, removing the residual mispricing that would otherwise persist. This mechanism may suppress the favorite-longshot bias entirely, or even invert it, in commodity markets specifically. Phase 2's prior should be **lower**, not higher, than the "unstudied universe = bigger opportunity" framing initially suggested.

**Why Phase 2 is gated on Phase 1:**

1. Phase 1 teaches the operational mechanics — fill-rate behavior, fee math, spread analysis, stop-rule discipline, toxic-flow detection — without the additional risk of an untested hypothesis on a less-favorable underlying market structure.
2. Phase 1's result *informs* Phase 2's odds. If the bias doesn't exist in long-dated geopolitics markets (where the spot-discipline counter-argument is weakest), the prior on it existing in commodity markets (where the counter-argument is strongest) weakens substantially. If Phase 1 succeeds in geopolitics, the prior on commodities is mixed: the bias might exist but be smaller, or it might be absent entirely.
3. Phase 2's analytical handle (stitched continuation: treating the May 1 oil contract and May 2 oil contract as samples of one underlying time series) requires infrastructure for multi-contract aggregation that Phase 1 will surface the need for.

**What Phase 2 would test:** to be specified after Phase 1 results. Rough shape: a maker-side longshot-selling and favorite-buying strategy applied to a stitched series of recurring contracts in commodity markets, with the underlying time series treated as one statistical sample rather than many independent ones. The first sub-test of Phase 2 should be specifically aimed at the spot-discipline question: does the Kalshi commodity market price systematically deviate from the underlying spot in a way that creates favorite-longshot bias, or does it track the spot tightly enough that no bias persists?

**This section is captured to preserve the research direction, not to commit to it.** Phase 2 only happens if Phase 1 succeeds. If Phase 1 fails, Phase 2's hypothesis is downgraded and may need its own pre-registered thesis document with a different framing.

## Future research directions, beyond Phase 2 (captured but not pursued)

These are alternative directions external review surfaced. They are documented here for intellectual honesty — to make clear they were considered and explicitly deferred — but they are *not* in scope for this thesis or for Phase 2.

- **Cross-platform Kalshi/Polymarket arbitrage.** Same event, two prices, trade the discrepancy. This is structurally simpler than the bias-capture strategy (no behavioral modeling, no microstructure assumptions — just price-difference detection). Deferred because: (a) the project does not currently have Polymarket integration, and rebuilding the discovery and pipeline work for a second platform is months of effort, (b) cross-platform arb requires capital on both platforms with the regulatory and operational complexity that brings, (c) the easy arbitrage opportunities in liquid event-pairs are likely already extracted by sophisticated participants. Worth revisiting only if Phase 1 and Phase 2 both fail and the project pivots to a different research direction.
- **AI-agent-driven information processing.** The "wall street pit" architecture vision. Deferred until at least one bundle (Phase 1 + Phase 2 if applicable) has produced a validated edge worth scaling. Building agents before having a working strategy is premature optimization.

---

## Baselines that Test B must beat

The strategy must outperform all of the following, not just the most flattering one:

1. **T-bill + 3pp** — the absolute success target. Beating this is the threshold for "thesis validated."
2. **Random selection within universe** — same number of trades, randomly chosen markets and sides. Tests whether the bias is real signal vs. statistical noise.
3. **Always-take-favorite** — buy YES on every market with YES price ≥0.85. Tests whether the strategy's longshot-selling adds value beyond simple favorite-buying.
4. **Always-sell-longshot** — sell YES on every market with YES price ≤0.15 (i.e., buy NO). Tests whether the strategy's favorite-buying adds value beyond simple longshot-selling.
5. **Hold cash** — the do-nothing baseline. Tests whether trading at all beats not trading.

If the strategy beats T-bill but loses to one of #2-#5, that's diagnostic information, not pass/fail — it tells us which component of the strategy carries the edge.

---

## Risks I am explicitly accepting

These are known risks I am choosing to test against rather than design around:

- **Bias decay.** Bürgi-Deng-Whelan note the bias is shrinking. The strategy may work today and not in 18 months. Test B's stop-rule (most-recent 3 months sub-cut) addresses this, but doesn't eliminate it.
- **Replication-crisis / regime-conditional risk.** The CEPR sample period (2021–2025) coincided with unusual macro volatility — post-COVID inflation, rapid Fed tightening cycle, banking stress (SVB, Credit Suisse). The favorite-longshot bias they documented may be partially a function of that regime, not a structural property of Kalshi. If the macro environment has shifted to a lower-volatility regime in 2026 onward, the bias may be substantially weaker or even inverted. Mitigation: Test B uses recent (last 6 months) data, capturing current-regime behavior. Sub-cuts by macro-volatility regime (high-VIX vs. low-VIX months) become a Session B design question.
- **Survivorship bias in the universe.** The discovery pulled markets that survived to settlement. Markets that delisted, got pulled, or had broken settlements are absent. This biases toward "well-behaved" markets and may inflate observed edge.
- **Maker fill-rate uncertainty.** The strategy relies on getting filled, and fills are conditional on someone wanting to take the other side. Fill-rate adverse selection (you get filled disproportionately when wrong) is a known pattern in market-making research.
- **Toxic flow / adverse selection on fills (Kyle 1985, Glosten-Milgrom 1985).** This is potentially the strongest argument against the strategy. In low-volatility macro markets, when a resting limit order fills, it is often because the world has changed faster than the order could be cancelled — a Fed official spoke, an unexpected number printed, geopolitical news hit the wires. Sophisticated low-latency traders see the news in milliseconds, see the now-stale limit order at a now-mispriced level, and pick it off before the cancel arrives. The maker is then providing cheap insurance to the informed flow rather than capturing the documented retail bias. The CEPR paper's finding that makers earn small positive returns provides empirical evidence that the bias overcomes this effect *in their sample*. Whether it still does in 2026 with more sophisticated participants is what Test B's adverse-selection stop-rule is designed to detect. Mitigation: stale-order cancellation rule, post-fill price-movement monitoring, abandonment if adverse selection exceeds threshold.
- **Susquehanna and other institutional makers.** They are doing the same thing the strategy proposes, with more capital, better latency, and tighter risk-management infrastructure. The thesis depends on there being enough residual mispricing for retail-size capital to find seats at the table after SIG has taken theirs. The toxic-flow risk above is the channel through which SIG-class participants extract value from retail makers.
- **Phase 2 spot-discipline risk** (forward-looking, applies if Phase 1 succeeds and Phase 2 is pursued). Phase 2's commodity markets (oil, gold, natgas) are anchored to liquid, well-arbed external spot markets. Unlike sports betting (where there is no "spot price for a horse" to discipline pricing), commodity Kalshi markets may inherit price efficiency from the underlying spot market, suppressing the favorite-longshot bias rather than amplifying it. This means Phase 2's prior should be lower, not higher, than the "first-mover unstudied universe" framing initially suggested.
- **Fee-schedule changes.** Kalshi can change fees. The current 7%/1.75% schedule is favorable for makers but is not contractual.
- **Platform/operational risk.** Kalshi is a relatively young exchange. Settlement disputes, regulatory shutdown, exchange failure, or pricing-engine bugs could damage trades. The 3pp success premium over T-bill assumes this risk is small (probably <50bps annualized in expectation given Kalshi's CFTC-regulated status), but it is non-zero.
- **My own confirmation bias.** I designed this thesis. Test B's pre-registered decision bands and the explicit baselines are the only protection against me unconsciously tuning the test toward a passing result.

---

## What changed across versions, summarized

| Aspect | v1 (YES-bias) | v2 (favorite-longshot) | v2.1 (this version) |
|---|---|---|---|
| Source of edge | Behavioral YES-preference | Structural + behavioral favorite-longshot bias | Same as v2 |
| Direction of trades | Take NO on overpriced YES | Sell longshots, buy favorites (maker-side) | Same as v2 |
| Fee model | Implicit, vague | Explicit Kalshi formula | Same as v2 |
| Academic anchor | None | Bürgi-Deng-Whelan (2026) | + Kyle, Glosten-Milgrom, Ottaviani-Sørensen, Rothschild |
| Maker vs. Taker | Phase 1b experiment | Core architecture | Same as v2, plus stale-order cancel rule |
| Success target | "3pp over random" (gut number) | T-bill + 3pp, locked, justified | Same as v2 |
| Internal contradiction | Engagement paradox | Resolved | Resolved (no change) |
| Toxic-flow / adverse selection | Not addressed | Not addressed | **Explicit kill condition + monitoring rule added** |
| Hold period | Implicit | Implicit | **Hold-to-settlement only, locked** |
| Phase 2 framing | N/A | Short-duration commodities, framed as "first-mover opportunity" | Phase 2 prior **lowered** by spot-discipline counter-argument |
| Replication-crisis risk | Not addressed | Not addressed | **Explicit acknowledgment in risks section** |
| Cross-platform arb | Not considered | Not considered | Captured as future direction, explicitly out of scope |

---

## How this document is used

- Committed to `notes/investment-thesis.md` before any test code exists.
- Referenced — not edited — during Session B (Test B pre-registration).
- Session B builds the test design *on* this thesis but does not contradict it. If a Session B design choice would contradict the thesis, the thesis is updated *first* and re-committed before any test code runs.
- After Test B (Session C), this document is the standard against which results are interpreted. The thesis predicted what *should* happen if the strategy works; the test confirms or refutes that prediction.

---

## References

Primary academic anchor:
- Bürgi, C., Deng, W., & Whelan, K. (2026). *Makers and Takers: The Economics of the Kalshi Prediction Market.* CEPR Discussion Paper 20631 / UCD School of Economics Working Paper 2025/19. https://www.ucd.ie/economics/t4media/WP2025_19.pdf

Foundational microstructure (informs the toxic-flow risk):
- Kyle, A. S. (1985). Continuous Auctions and Insider Trading. *Econometrica*, 53(6), 1315-1335.
- Glosten, L. R., & Milgrom, P. R. (1985). Bid, ask and transaction prices in a specialist market with heterogeneously informed traders. *Journal of Financial Economics*, 14(1), 71-100.

Favorite-longshot bias literature:
- Ottaviani, M., & Sørensen, P. N. (2008). The Favorite-Longshot Bias: An Overview of the Main Explanations. In *Handbook of Sports and Lottery Markets*, Elsevier. (Risk-love vs. misperception of small probabilities — the two main competing explanations of the bias.)
- Snowberg, E., & Wolfers, J. (2010). Explaining the Favorite-Longshot Bias: Is it Risk-Love or Misperceptions? *Journal of Political Economy*, 118(4), 723-746.

Election-market microstructure (relevant for the geopolitics sub-universe):
- Rothschild, D. (2009). Forecasting Elections: Comparing Prediction Markets, Polls, and Their Biases. *Public Opinion Quarterly*, 73(5), 895-916.

Macro-context validation:
- Diercks, A. M., Katz, J. D., & Wright, J. (2026). *Kalshi and the Rise of Macro Markets.* Federal Reserve Finance and Economics Discussion Series 2026-010.

Operational reference:
- Kalshi Fee Schedule (May 2026). https://kalshi.com/fee-schedule

---

*Written before any test code exists. The discipline is: thesis first, baselines second, parameters locked, then code. v2.1 incorporates two rounds of adversarial external review. The reframe from v1 to v2 was forced by round 1 surfacing a published academic paper that documented the actual pattern. The targeted edits in v2.1 were forced by round 2 surfacing the toxic-flow / adverse-selection risk and refining the Phase 2 framing. Test B is now a replication-and-extension of an established finding, not the invention of a new one — with explicit kill conditions for the most likely failure modes the established literature has identified.*
