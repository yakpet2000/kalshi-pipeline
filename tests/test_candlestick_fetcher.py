"""Unit tests for scripts/fetch_candlesticks.py — mocked HTTP.

Tests cover the retry/backoff/pagination logic specified in the
2b.2 sub-stage plan. No live Kalshi API calls.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "fetch_candlesticks", PROJECT_ROOT / "scripts" / "fetch_candlesticks.py"
)
fetcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetcher)


# A trivial private-key stand-in. The auth_headers code is exercised by the
# real Kalshi flow at smoke time; for unit tests, we patch sign_request and
# auth_headers via the fetcher's reference so we don't need real RSA keys.
class _DummyKey:
    pass


def _mk_response(status: int, body: dict | str = "") -> MagicMock:
    """Build a MagicMock that quacks like an httpx.Response."""
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    if isinstance(body, dict):
        r.json.return_value = body
        r.text = ""
    else:
        r.json.side_effect = ValueError("no JSON")
        r.text = body
    return r


@pytest.fixture
def patched_auth():
    """Patch auth_headers in the fetcher module so tests don't need a
    real private key. Returns nothing useful; used as a side-effect
    fixture."""
    with patch.object(fetcher, "auth_headers", return_value={"X-Test": "1"}):
        yield


@pytest.fixture
def patched_sleep():
    """Patch time.sleep inside the fetcher module to a no-op so tests
    run instantly. Returns the mock so call counts can be inspected."""
    with patch.object(fetcher.time, "sleep") as m:
        yield m


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------


def test_429_triggers_retry_then_succeeds(patched_auth, patched_sleep):
    """A 429 response is retried with backoff; once a 200 returns, the
    fetcher succeeds and parses the body."""
    client = MagicMock()
    client.get.side_effect = [
        _mk_response(429),
        _mk_response(200, {"ticker": "T1", "candlesticks": []}),
    ]
    out = fetcher.fetch_one_market(
        client, "key_id", _DummyKey(), "S1", "T1", 0, 1
    )
    assert out == {"ticker": "T1", "candlesticks": []}
    assert client.get.call_count == 2


def test_503_triggers_retry_then_succeeds(patched_auth, patched_sleep):
    """A 503 (server error in 5xx range) is retried; subsequent 200
    succeeds."""
    client = MagicMock()
    client.get.side_effect = [
        _mk_response(503),
        _mk_response(200, {"ticker": "T1", "candlesticks": []}),
    ]
    out = fetcher.fetch_one_market(
        client, "key_id", _DummyKey(), "S1", "T1", 0, 1
    )
    assert out["ticker"] == "T1"
    assert client.get.call_count == 2


def test_404_fails_immediately_no_retry(patched_auth, patched_sleep):
    """A 404 response is non-retryable; the fetcher raises FetchError
    on the first call without retrying."""
    client = MagicMock()
    client.get.return_value = _mk_response(404, "not found")
    with pytest.raises(fetcher.FetchError, match="404"):
        fetcher.fetch_one_market(
            client, "key_id", _DummyKey(), "S1", "T1", 0, 1
        )
    assert client.get.call_count == 1


def test_401_fails_immediately_no_retry(patched_auth, patched_sleep):
    """A 401 response is non-retryable; raises immediately."""
    client = MagicMock()
    client.get.return_value = _mk_response(401, "unauthorized")
    with pytest.raises(fetcher.FetchError, match="401"):
        fetcher.fetch_one_market(
            client, "key_id", _DummyKey(), "S1", "T1", 0, 1
        )
    assert client.get.call_count == 1


def test_403_fails_immediately_no_retry(patched_auth, patched_sleep):
    """A 403 response is non-retryable; raises immediately."""
    client = MagicMock()
    client.get.return_value = _mk_response(403, "forbidden")
    with pytest.raises(fetcher.FetchError, match="403"):
        fetcher.fetch_one_market(
            client, "key_id", _DummyKey(), "S1", "T1", 0, 1
        )
    assert client.get.call_count == 1


def test_repeated_429_eventually_exhausts_and_raises(patched_auth, patched_sleep):
    """If 429 keeps recurring beyond BACKOFF_SCHEDULE retries, the
    fetcher exhausts and raises FetchError."""
    client = MagicMock()
    # First call (attempt=0, no sleep) + one per backoff slot = 4 total
    client.get.return_value = _mk_response(429)
    with pytest.raises(fetcher.FetchError, match="exhausted"):
        fetcher.fetch_one_market(
            client, "key_id", _DummyKey(), "S1", "T1", 0, 1
        )
    assert client.get.call_count == 1 + len(fetcher.BACKOFF_SCHEDULE)


# ---------------------------------------------------------------------------
# Pagination / cursor
# ---------------------------------------------------------------------------


def test_pagination_concatenates_pages(patched_auth, patched_sleep):
    """If the response includes a non-empty `cursor`, the fetcher
    follows it, and the candlesticks arrays from all pages are
    concatenated in order."""
    client = MagicMock()
    page1 = {
        "ticker": "T1",
        "candlesticks": [
            {"end_period_ts": 1000, "volume_fp": "5.00"},
            {"end_period_ts": 2000, "volume_fp": "0.00"},
        ],
        "cursor": "PAGE2",
    }
    page2 = {
        "ticker": "T1",
        "candlesticks": [
            {"end_period_ts": 3000, "volume_fp": "3.00"},
        ],
        # no cursor on the last page
    }
    client.get.side_effect = [_mk_response(200, page1), _mk_response(200, page2)]
    out = fetcher.fetch_one_market(
        client, "key_id", _DummyKey(), "S1", "T1", 0, 9999
    )
    assert out["ticker"] == "T1"
    assert len(out["candlesticks"]) == 3
    # Order preserved: page 1 candles before page 2 candles
    ts_seq = [c["end_period_ts"] for c in out["candlesticks"]]
    assert ts_seq == [1000, 2000, 3000]
    assert client.get.call_count == 2


def test_single_page_response_no_cursor(patched_auth, patched_sleep):
    """A response with no `cursor` field returns immediately after
    the first page."""
    client = MagicMock()
    body = {
        "ticker": "T1",
        "candlesticks": [{"end_period_ts": 1000, "volume_fp": "5.00"}],
    }
    client.get.return_value = _mk_response(200, body)
    out = fetcher.fetch_one_market(
        client, "key_id", _DummyKey(), "S1", "T1", 0, 1
    )
    assert len(out["candlesticks"]) == 1
    assert client.get.call_count == 1


def test_empty_candles_terminates_pagination(patched_auth, patched_sleep):
    """A page with empty `candlesticks` terminates pagination even if
    a cursor is present (defensive: empty page = stop)."""
    client = MagicMock()
    body = {"ticker": "T1", "candlesticks": [], "cursor": "WOULD_LOOP"}
    client.get.return_value = _mk_response(200, body)
    out = fetcher.fetch_one_market(
        client, "key_id", _DummyKey(), "S1", "T1", 0, 1
    )
    assert out["candlesticks"] == []
    assert client.get.call_count == 1


# ---------------------------------------------------------------------------
# Window-to-Unix conversion
# ---------------------------------------------------------------------------


def test_window_to_unix_pads_one_day_each_side():
    """window_to_unix returns timestamps padded by 1 day on each side
    of the parsed ISO inputs (matches build_test_b_universe.py
    convention)."""
    start_ts, end_ts = fetcher.window_to_unix(
        "2026-01-15T00:00:00Z", "2026-01-20T00:00:00Z"
    )
    # 5 days requested (15 -> 20) + 2 day padding (1 each side) = 7 days
    assert end_ts - start_ts == 7 * 86400
