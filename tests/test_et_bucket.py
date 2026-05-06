"""Unit tests for simulator.et_bucket — ET-bucket date labeling.

Per notes/candle-data-probe.md §5 a candle ending at midnight US
Eastern Time covers the prior calendar date in ET. Tests cover both
EDT (UTC-4, summer) and EST (UTC-5, winter) cases.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from simulator.et_bucket import et_bucket_date


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())


def test_edt_summer_candle_labels_prior_et_date():
    """A candle ending 2026-04-15T04:00:00Z (= 2026-04-15T00:00 EDT)
    covers ET-day 2026-04-14 — the candle's interval is from
    2026-04-14T00:00 ET to 2026-04-15T00:00 ET."""
    ts = _ts(2026, 4, 15, 4)
    assert et_bucket_date(ts) == date(2026, 4, 14)


def test_est_winter_candle_labels_prior_et_date():
    """A candle ending 2026-01-07T05:00:00Z (= 2026-01-07T00:00 EST)
    covers ET-day 2026-01-06."""
    ts = _ts(2026, 1, 7, 5)
    assert et_bucket_date(ts) == date(2026, 1, 6)


def test_dst_transition_spring_forward():
    """Around the spring-forward transition (2026-03-08), the candle
    ending 2026-03-08T05:00:00Z is the last EST candle (covers
    2026-03-07 ET); the candle ending 2026-03-09T04:00:00Z is the
    first EDT candle (covers 2026-03-08 ET)."""
    last_est = _ts(2026, 3, 8, 5)
    first_edt = _ts(2026, 3, 9, 4)
    assert et_bucket_date(last_est) == date(2026, 3, 7)
    assert et_bucket_date(first_edt) == date(2026, 3, 8)


def test_returns_date_not_datetime():
    """The labeling function returns a plain `date`, not a `datetime`."""
    ts = _ts(2026, 4, 15, 4)
    out = et_bucket_date(ts)
    assert isinstance(out, date)
    assert not isinstance(out, datetime)
