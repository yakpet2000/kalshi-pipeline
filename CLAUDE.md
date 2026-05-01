# kalshi-pipeline

A data pipeline that snapshots Kalshi prediction market prices every 15 minutes,
stores them in Postgres, and answers two queries: a stale-price detector and a
biggest-movers sanity check.

This is an exercise project. Agents, trading, LLMs, and news ingestion are
explicit non-goals at this phase.

## Status

Early development. Four planned sessions:

1. Skeleton: schema SQL, `db.py`, `kalshi_client.py`, `tracked_markets.yml` template.
2. Collector + first real run.
3. Query layer (stale detector + biggest movers).
4. Deploy to remote server + cron.

Session 1 (skeleton) is complete. We are currently in **session 2** (collector + first real run).

## What this pipeline does

- Polls the Kalshi public REST API every 15 minutes for a hand-picked list of
  15–30 markets (tickers declared in `tracked_markets.yml` at repo root). Entries
  in this file are sub-tickers (terminal markets), not Kalshi event tickers;
  resolve events to their children via `GET /events/{event_ticker}` before adding.
- Writes one snapshot row per tracked market per poll to `market_snapshots`.
- Refreshes `market_metadata` daily (slower-changing fields).
- Exposes two queries:
  - **Headline:** stale-price detector — markets whose recent price volatility
    is statistically low relative to their own historical baseline. Candidates
    for mispricing that a future agent would investigate.
  - **Control:** biggest movers in the last 24 hours. Used to test whether the
    stale detector is just a lagging version of "things that already moved."

## Data source

- Kalshi public REST API, no authentication required.
- Base URL: `https://api.elections.kalshi.com/trade-api/v2`
- Primary endpoints: `GET /markets`, `GET /markets/{ticker}`
- **Use the `_fp` and `_dollars` fields.** Both are suffixes on decimal
  strings, not integer fixed-point: `_dollars` denotes a USD price
  (4 decimals, e.g. `"0.2500"`) and `_fp` denotes a quantity like volume
  or open interest (2 decimals, e.g. `"1234.00"`). Store both as `NUMERIC`
  in Postgres, not `INTEGER`. The legacy integer fields (e.g. `yes_bid`,
  `yes_ask` without suffix) were deprecated on 2026-03-12 and should not
  be read by new code.

## Schema design

Two tables:

### `market_snapshots` (high-volume, narrow)
- Primary key: `(ticker, observed_at)`
- One row per tracked market per 15-minute snapshot.
- `observed_at` is always truncated to a 15-minute bucket in UTC.
- Includes a `raw_payload JSONB` column with the full API response so we can
  backfill derived columns later without re-fetching.
- Inserts use `ON CONFLICT (ticker, observed_at) DO NOTHING` for idempotency.

### `market_metadata` (low-volume, wider)
- Primary key: `ticker`
- Refreshed daily, not every snapshot.
- Includes `raw_payload JSONB` for the same reason.

## Conventions

- **Python 3.11+**, currently running 3.14.3 in `.venv/`.
- **Postgres** via `psycopg` v3 (not psycopg2).
- **pydantic** for request/response validation.
- **structlog** for structured logging.
  - Renderer is env-gated: `ENV=dev` (default) selects `structlog.dev.ConsoleRenderer`;
    any other `ENV` value selects `structlog.processors.JSONRenderer` for cron/prod.
- **UTC everywhere in storage.** Never store naive datetimes. Never store
  local time. Conversions happen at display time only.
- Secrets and connection strings live in `.env` (gitignored).
  `.env.example` is committed as a template.
- `DATABASE_URL` uses the `postgresql://` scheme, not the legacy `postgres://`
  form. Both work with psycopg v3 today, but `postgresql://` is canonical.

## Non-goals (hard)

- No trading, no order placement, no authenticated Kalshi endpoints.
- No LLM calls, no agents, no model inference of any kind.
- No news scraping or external signal ingestion.
- No web UI or dashboard. Queries are run from a CLI or psql.
- No paper trading simulation.

If a task seems to drift toward any of these, stop and flag it.

## Working style with Claude Code

- Before writing code in a new session, read this file and confirm which
  session we are on.
- Prefer small, reviewable diffs. One concern per commit.
- Do not add dependencies without flagging them. The stack above is the
  stack; additions need a reason.
- Do not "improve" or restructure this CLAUDE.md without being asked.
