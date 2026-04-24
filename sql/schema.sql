-- Schema for kalshi-pipeline.
-- Two tables: market_snapshots (15-min time series) and market_metadata (daily refresh).
-- Apply with: psql "$DATABASE_URL" -f sql/schema.sql

CREATE TABLE IF NOT EXISTS market_snapshots (
    ticker                      TEXT        NOT NULL,
    observed_at                 TIMESTAMPTZ NOT NULL,
    updated_time                TIMESTAMPTZ,
    inserted_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    yes_bid_dollars             NUMERIC(10, 4),
    yes_ask_dollars             NUMERIC(10, 4),
    no_bid_dollars              NUMERIC(10, 4),
    no_ask_dollars              NUMERIC(10, 4),
    last_price_dollars          NUMERIC(10, 4),
    previous_price_dollars      NUMERIC(10, 4),
    previous_yes_bid_dollars    NUMERIC(10, 4),
    previous_yes_ask_dollars    NUMERIC(10, 4),

    volume_fp                   NUMERIC(20, 2),
    volume_24h_fp               NUMERIC(20, 2),
    open_interest_fp            NUMERIC(20, 2),
    yes_bid_size_fp             NUMERIC(20, 2),
    yes_ask_size_fp             NUMERIC(20, 2),

    raw_payload                 JSONB       NOT NULL,

    PRIMARY KEY (ticker, observed_at)
);

COMMENT ON COLUMN market_snapshots.observed_at IS
    'Poll time floored to the nearest 15-minute UTC bucket (:00/:15/:30/:45). Part of the primary key and the basis for all time-series queries.';
COMMENT ON COLUMN market_snapshots.updated_time IS
    'API-reported timestamp of the last change to this market, from the Kalshi response. May lag observed_at if the market is quiet.';
COMMENT ON COLUMN market_snapshots.inserted_at IS
    'Wall-clock time when this row landed in Postgres, DEFAULT NOW(). Diagnostic only — do not use for time-series queries; use observed_at.';
COMMENT ON COLUMN market_snapshots.previous_price_dollars IS
    'Kalshi-reported prior value for last_price_dollars. Semantics defined by Kalshi; stored as-returned.';
COMMENT ON COLUMN market_snapshots.previous_yes_bid_dollars IS
    'Kalshi-reported prior value for yes_bid_dollars. Semantics defined by Kalshi.';
COMMENT ON COLUMN market_snapshots.previous_yes_ask_dollars IS
    'Kalshi-reported prior value for yes_ask_dollars. Semantics defined by Kalshi.';
COMMENT ON COLUMN market_snapshots.volume_fp IS
    'Cumulative contract volume since market opened, as reported by Kalshi.';
COMMENT ON COLUMN market_snapshots.volume_24h_fp IS
    'Rolling contract volume over the last 24 hours, as reported by Kalshi.';
COMMENT ON COLUMN market_snapshots.raw_payload IS
    'Full Kalshi API response for this market at this snapshot. Source of truth for any field not promoted to a column; used to backfill new columns without re-fetching.';

CREATE INDEX IF NOT EXISTS market_snapshots_observed_at_idx
    ON market_snapshots (observed_at DESC);


CREATE TABLE IF NOT EXISTS market_metadata (
    ticker                      TEXT        PRIMARY KEY,
    event_ticker                TEXT        NOT NULL,
    series_ticker               TEXT,
    title                       TEXT        NOT NULL,
    market_type                 TEXT        NOT NULL,
    status                      TEXT        NOT NULL,
    tick_size                   INTEGER,
    open_time                   TIMESTAMPTZ,
    close_time                  TIMESTAMPTZ,
    expected_expiration_time    TIMESTAMPTZ,
    raw_payload                 JSONB       NOT NULL,
    first_seen_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_refreshed_at           TIMESTAMPTZ NOT NULL
);

COMMENT ON COLUMN market_metadata.series_ticker IS
    'Kalshi series grouping. Nullable — not all markets belong to a series.';
COMMENT ON COLUMN market_metadata.status IS
    'Kalshi-reported status (e.g. active, closed, settled). Promoted from raw_payload so we can filter without a JSONB probe.';
COMMENT ON COLUMN market_metadata.tick_size IS
    'Minimum price increment as reported by Kalshi, integer. Unit is ambiguous in the sampled market: tick_size=1 appeared alongside response_price_units="usd_cent" (suggests $0.01) and price_level_structure="deci_cent" with price_ranges step "0.0010" (suggests $0.001). Nullable until we sample more markets and resolve the unit. Used by the stale-price detector to normalize moves across markets.';
COMMENT ON COLUMN market_metadata.raw_payload IS
    'Full Kalshi API response from the most recent daily refresh. Source of truth for fields not promoted to columns.';
COMMENT ON COLUMN market_metadata.first_seen_at IS
    'First time this ticker appeared in our daily metadata refresh. Set once via DEFAULT NOW().';
COMMENT ON COLUMN market_metadata.last_refreshed_at IS
    'Timestamp of the most recent daily metadata refresh. Set explicitly by the refresh job on every upsert.';
