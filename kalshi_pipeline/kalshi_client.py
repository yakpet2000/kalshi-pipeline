"""HTTP client for the public Kalshi markets API.

Exposes a single KalshiClient class that fetches market data via httpx. Parses
responses through Pydantic models (Market, MarketDetailResponse) configured with
extra='allow' so unknown or newly-added Kalshi fields are preserved rather than
silently dropped.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import TracebackType

import httpx
from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator


class Market(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticker: str
    event_ticker: str
    title: str
    market_type: str
    status: str
    updated_time: datetime

    series_ticker: str | None = None
    tick_size: int | None = None
    open_time: datetime | None = None
    close_time: datetime | None = None
    expected_expiration_time: datetime | None = None

    yes_bid_dollars: Decimal | None = None
    yes_ask_dollars: Decimal | None = None
    no_bid_dollars: Decimal | None = None
    no_ask_dollars: Decimal | None = None
    last_price_dollars: Decimal | None = None
    previous_price_dollars: Decimal | None = None
    previous_yes_bid_dollars: Decimal | None = None
    previous_yes_ask_dollars: Decimal | None = None

    volume_fp: Decimal | None = None
    volume_24h_fp: Decimal | None = None
    open_interest_fp: Decimal | None = None
    yes_bid_size_fp: Decimal | None = None
    yes_ask_size_fp: Decimal | None = None

    @field_validator(
        "updated_time",
        "open_time",
        "close_time",
        "expected_expiration_time",
        mode="before",
    )
    @classmethod
    def _empty_string_to_none(cls, value: object, info: ValidationInfo) -> object:
        if value != "":
            return value
        if info.field_name == "updated_time":
            ticker = info.data.get("ticker", "<unknown>")
            raise ValueError(f"empty updated_time for ticker {ticker}")
        return None


class MarketDetailResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    market: Market


class KalshiClient:
    """Client for the public Kalshi REST API.

    Owns an ``httpx.Client`` for the life of this object. Use as a context manager
    (``with KalshiClient() as c:``) or close explicitly via ``.close()``. Holding the
    httpx.Client as an instance attribute lets HTTP keepalive amortize across the
    15–30 markets polled each tick, without asking callers to manage its lifecycle
    on every request.
    """

    def __init__(
        self,
        base_url: str = "https://api.elections.kalshi.com/trade-api/v2",
        timeout: float = 10.0,
    ) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def __enter__(self) -> KalshiClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_market(self, ticker: str) -> Market:
        """Fetch ``/markets/{ticker}`` and return the parsed ``Market``.

        Raises ``httpx.HTTPStatusError`` on non-2xx. Callers that also need the raw
        payload for storage should call ``market.model_dump(mode="json")`` — this
        round-trips Decimals back to strings, matching the form Kalshi sent.
        """
        response = self._client.get(f"/markets/{ticker}")
        response.raise_for_status()
        return MarketDetailResponse.model_validate(response.json()).market


# TODO(session-2): add get_markets(tickers) batched fetch if per-tick latency becomes noticeable.
