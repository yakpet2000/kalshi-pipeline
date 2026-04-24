"""Database layer for kalshi-pipeline.

Provides a connection factory and bulk insert/upsert helpers for the two tables
defined in sql/schema.sql. Input rows are plain dataclasses that mirror the
schema columns one-to-one; conversion to SQL parameters happens here.
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

load_dotenv()


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    ticker: str
    observed_at: datetime
    updated_time: datetime | None
    yes_bid_dollars: Decimal | None
    yes_ask_dollars: Decimal | None
    no_bid_dollars: Decimal | None
    no_ask_dollars: Decimal | None
    last_price_dollars: Decimal | None
    previous_price_dollars: Decimal | None
    previous_yes_bid_dollars: Decimal | None
    previous_yes_ask_dollars: Decimal | None
    volume_fp: Decimal | None
    volume_24h_fp: Decimal | None
    open_interest_fp: Decimal | None
    yes_bid_size_fp: Decimal | None
    yes_ask_size_fp: Decimal | None
    raw_payload: dict[str, Any]
    inserted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MetadataRow:
    ticker: str
    event_ticker: str
    series_ticker: str | None
    title: str
    market_type: str
    status: str
    tick_size: int | None
    open_time: datetime | None
    close_time: datetime | None
    expected_expiration_time: datetime | None
    raw_payload: dict[str, Any]
    last_refreshed_at: datetime
    first_seen_at: datetime | None = None


_SNAPSHOT_INSERT_COLUMNS = (
    "ticker",
    "observed_at",
    "updated_time",
    "yes_bid_dollars",
    "yes_ask_dollars",
    "no_bid_dollars",
    "no_ask_dollars",
    "last_price_dollars",
    "previous_price_dollars",
    "previous_yes_bid_dollars",
    "previous_yes_ask_dollars",
    "volume_fp",
    "volume_24h_fp",
    "open_interest_fp",
    "yes_bid_size_fp",
    "yes_ask_size_fp",
    "raw_payload",
)

_METADATA_INSERT_COLUMNS = (
    "ticker",
    "event_ticker",
    "series_ticker",
    "title",
    "market_type",
    "status",
    "tick_size",
    "open_time",
    "close_time",
    "expected_expiration_time",
    "raw_payload",
    "last_refreshed_at",
)

_METADATA_UPDATE_COLUMNS = tuple(
    c for c in _METADATA_INSERT_COLUMNS if c != "ticker"
)


def connect() -> psycopg.Connection:
    """Open a new Postgres connection. Caller must use it as a context manager (``with connect() as conn:``); in psycopg v3, exiting the ``with`` block commits on success and rolls back on exception."""
    return psycopg.connect(os.environ["DATABASE_URL"])


def floor_to_15min(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError(
            "floor_to_15min requires a tz-aware datetime; got naive. "
            "All times in this pipeline must be tz-aware UTC."
        )
    dt_utc = dt.astimezone(UTC)
    floored_minute = (dt_utc.minute // 15) * 15
    return dt_utc.replace(minute=floored_minute, second=0, microsecond=0)


def insert_snapshots(conn: psycopg.Connection, rows: Iterable[SnapshotRow]) -> int:
    params_per_row = [_snapshot_to_params(r) for r in rows]
    if not params_per_row:
        return 0
    columns = ", ".join(_SNAPSHOT_INSERT_COLUMNS)
    row_placeholder = "(" + ", ".join(["%s"] * len(_SNAPSHOT_INSERT_COLUMNS)) + ")"
    values_clause = ", ".join([row_placeholder] * len(params_per_row))
    flat_params: list[Any] = []
    for row_params in params_per_row:
        flat_params.extend(row_params)
    sql = (
        f"INSERT INTO market_snapshots ({columns}) VALUES {values_clause} "
        f"ON CONFLICT (ticker, observed_at) DO NOTHING RETURNING ticker"
    )
    with conn.cursor() as cur:
        cur.execute(sql, flat_params)
        return len(cur.fetchall())


def upsert_metadata(conn: psycopg.Connection, rows: Iterable[MetadataRow]) -> int:
    params = [_metadata_to_params(r) for r in rows]
    if not params:
        return 0
    columns = ", ".join(_METADATA_INSERT_COLUMNS)
    placeholders = ", ".join(["%s"] * len(_METADATA_INSERT_COLUMNS))
    set_clause = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in _METADATA_UPDATE_COLUMNS
    )
    sql = (
        f"INSERT INTO market_metadata ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT (ticker) DO UPDATE SET {set_clause}"
    )
    with conn.cursor() as cur:
        cur.executemany(sql, params)
        return cur.rowcount


def _snapshot_to_params(row: SnapshotRow) -> tuple[Any, ...]:
    return (
        row.ticker,
        row.observed_at,
        row.updated_time,
        row.yes_bid_dollars,
        row.yes_ask_dollars,
        row.no_bid_dollars,
        row.no_ask_dollars,
        row.last_price_dollars,
        row.previous_price_dollars,
        row.previous_yes_bid_dollars,
        row.previous_yes_ask_dollars,
        row.volume_fp,
        row.volume_24h_fp,
        row.open_interest_fp,
        row.yes_bid_size_fp,
        row.yes_ask_size_fp,
        Jsonb(row.raw_payload),
    )


def _metadata_to_params(row: MetadataRow) -> tuple[Any, ...]:
    return (
        row.ticker,
        row.event_ticker,
        row.series_ticker,
        row.title,
        row.market_type,
        row.status,
        row.tick_size,
        row.open_time,
        row.close_time,
        row.expected_expiration_time,
        Jsonb(row.raw_payload),
        row.last_refreshed_at,
    )
