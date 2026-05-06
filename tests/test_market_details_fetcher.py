"""Unit tests for scripts/fetch_market_details.py — mocked HTTP.

No live Kalshi API calls. Mirrors the structure of
tests/test_candlestick_fetcher.py.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "fetch_market_details", PROJECT_ROOT / "scripts" / "fetch_market_details.py"
)
fetcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetcher)


class _DummyKey:
    pass


def _mk_response(status: int, body: dict | str = "") -> MagicMock:
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
    with patch.object(fetcher, "auth_headers", return_value={"X-Test": "1"}):
        yield


@pytest.fixture
def patched_sleep():
    with patch.object(fetcher.time, "sleep") as m:
        yield m


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------


def test_429_triggers_retry_then_succeeds(patched_auth, patched_sleep):
    """429 is retried with backoff; subsequent 200 succeeds."""
    client = MagicMock()
    client.get.side_effect = [
        _mk_response(429),
        _mk_response(200, {"market": {"ticker": "T1",
                                       "settlement_value_dollars": "1.0000",
                                       "result": "yes"}}),
    ]
    out = fetcher.fetch_one_market(client, "key_id", _DummyKey(), "T1")
    assert out["market"]["settlement_value_dollars"] == "1.0000"
    assert client.get.call_count == 2


def test_503_triggers_retry_then_succeeds(patched_auth, patched_sleep):
    """503 is retried with backoff; subsequent 200 succeeds."""
    client = MagicMock()
    client.get.side_effect = [
        _mk_response(503),
        _mk_response(200, {"market": {"ticker": "T1",
                                       "settlement_value_dollars": "0.0000",
                                       "result": "no"}}),
    ]
    out = fetcher.fetch_one_market(client, "key_id", _DummyKey(), "T1")
    assert out["market"]["result"] == "no"
    assert client.get.call_count == 2


def test_404_fails_immediately(patched_auth, patched_sleep):
    """404 (e.g., the rare unreachable ticker) is non-retryable."""
    client = MagicMock()
    client.get.return_value = _mk_response(404, "not found")
    with pytest.raises(fetcher.FetchError, match="404"):
        fetcher.fetch_one_market(client, "key_id", _DummyKey(), "T-MISSING")
    assert client.get.call_count == 1


def test_401_fails_immediately(patched_auth, patched_sleep):
    """401 (auth failure) is non-retryable."""
    client = MagicMock()
    client.get.return_value = _mk_response(401, "unauthorized")
    with pytest.raises(fetcher.FetchError, match="401"):
        fetcher.fetch_one_market(client, "key_id", _DummyKey(), "T1")
    assert client.get.call_count == 1


def test_403_fails_immediately(patched_auth, patched_sleep):
    """403 (forbidden) is non-retryable."""
    client = MagicMock()
    client.get.return_value = _mk_response(403, "forbidden")
    with pytest.raises(fetcher.FetchError, match="403"):
        fetcher.fetch_one_market(client, "key_id", _DummyKey(), "T1")
    assert client.get.call_count == 1


def test_repeated_429_eventually_exhausts_and_raises(patched_auth, patched_sleep):
    """Persistent 429 raises after BACKOFF_SCHEDULE is exhausted."""
    client = MagicMock()
    client.get.return_value = _mk_response(429)
    with pytest.raises(fetcher.FetchError, match="exhausted"):
        fetcher.fetch_one_market(client, "key_id", _DummyKey(), "T1")
    assert client.get.call_count == 1 + len(fetcher.BACKOFF_SCHEDULE)


def test_non_json_200_raises(patched_auth, patched_sleep):
    """A 200 response that isn't JSON raises FetchError."""
    client = MagicMock()
    client.get.return_value = _mk_response(200, "not json")
    with pytest.raises(fetcher.FetchError, match="non-JSON"):
        fetcher.fetch_one_market(client, "key_id", _DummyKey(), "T1")


# ---------------------------------------------------------------------------
# Successful happy-path
# ---------------------------------------------------------------------------


def test_single_call_happy_path(patched_auth, patched_sleep):
    """A single 200 returns the body without retries."""
    client = MagicMock()
    body = {"market": {"ticker": "T1",
                       "settlement_value_dollars": "0.0000",
                       "result": "no",
                       "status": "finalized"}}
    client.get.return_value = _mk_response(200, body)
    out = fetcher.fetch_one_market(client, "key_id", _DummyKey(), "T1")
    assert out == body
    assert client.get.call_count == 1
