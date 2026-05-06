"""ET-bucket date labeling for Kalshi daily candles.

Per notes/candle-data-probe.md §5, Kalshi's daily candles end at
midnight US Eastern Time (04:00 UTC during EDT, 05:00 UTC during EST).
A candle ending at 2026-04-15T04:00:00Z (EDT) covers ET-day
2026-04-14 (00:00 ET to 24:00 ET on April 14) and is labeled
2026-04-14.

This module is the single source of truth for that date labeling
inside the simulator. It is used by the daily-check engine
(simulator/daily_check.py, future 2b.3) and any analysis stage that
maps candle timestamps to fill/settlement dates.

Implementation note: we subtract one second from the candle's
end_period_ts (placing the instant inside the candle's interval),
then convert to America/New_York and take the date. zoneinfo handles
DST transitions automatically.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def et_bucket_date(end_period_ts: int) -> date:
    """Return the ET-bucket calendar date a candle covers, given its
    end_period_ts (Unix epoch seconds, UTC).

    Examples (per candle-data-probe.md §5):
      2026-04-15T04:00:00Z (EDT) -> date(2026, 4, 14)
      2026-01-07T05:00:00Z (EST) -> date(2026, 1, 6)
    """
    instant = datetime.fromtimestamp(end_period_ts, tz=timezone.utc) - timedelta(seconds=1)
    return instant.astimezone(ET).date()
