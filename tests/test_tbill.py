"""Unit tests for simulator.tbill — FRED DGS3MO lookup with forward-fill.

Tests are driven by `tests/fixtures/dgs3mo_sample.csv`, a hand-curated
slice of real FRED DGS3MO data covering known business days, weekends,
and federal holidays.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from simulator import tbill

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dgs3mo_sample.csv"


@pytest.fixture(autouse=True)
def _isolate_module_cache():
    """Clear simulator.tbill's module-level cache between tests so each
    test starts from a clean slate. Otherwise a stale entry from a prior
    test could mask a bug."""
    tbill._loaded_cache.clear()
    yield
    tbill._loaded_cache.clear()


def test_known_business_day():
    """A standard business day returns the published rate verbatim.
    Verified against FRED published value for 2025-06-16 (DGS3MO=4.43)."""
    rate = tbill.tbill_rate(date(2025, 6, 16), csv_path=FIXTURE)
    assert rate == Decimal("0.0443")


def test_returns_decimal_not_float():
    """Decimal precision: return type must be Decimal, not float."""
    rate = tbill.tbill_rate(date(2025, 6, 16), csv_path=FIXTURE)
    assert isinstance(rate, Decimal)
    assert not isinstance(rate, float)


def test_saturday_forward_fills_to_prior_friday():
    """2025-06-14 (Saturday) forward-fills to 2025-06-13 (Friday, 4.45%)."""
    rate = tbill.tbill_rate(date(2025, 6, 14), csv_path=FIXTURE)
    assert rate == Decimal("0.0445")


def test_sunday_forward_fills_to_prior_friday():
    """2025-06-15 (Sunday) forward-fills to 2025-06-13 (Friday, 4.45%)."""
    rate = tbill.tbill_rate(date(2025, 6, 15), csv_path=FIXTURE)
    assert rate == Decimal("0.0445")


def test_new_years_day_forward_fills_to_prior_business_day():
    """2025-01-01 (federal holiday, empty cell in FRED CSV) forward-fills
    to 2024-12-31 (Tuesday, 4.37%)."""
    rate = tbill.tbill_rate(date(2025, 1, 1), csv_path=FIXTURE)
    assert rate == Decimal("0.0437")


def test_memorial_day_forward_fills_to_prior_friday():
    """2025-05-26 (Memorial Day, empty cell) forward-fills to 2025-05-23
    (Friday, 4.36%)."""
    rate = tbill.tbill_rate(date(2025, 5, 26), csv_path=FIXTURE)
    assert rate == Decimal("0.0436")


def test_christmas_forward_fills_to_prior_business_day():
    """2025-12-25 (Christmas, empty cell) forward-fills to 2025-12-24
    (Wednesday, 3.69%)."""
    rate = tbill.tbill_rate(date(2025, 12, 25), csv_path=FIXTURE)
    assert rate == Decimal("0.0369")


def test_out_of_range_before_earliest_raises():
    """Date before the earliest cached row raises ValueError, not
    silently returning zero."""
    with pytest.raises(ValueError, match="before the earliest"):
        tbill.tbill_rate(date(2020, 1, 1), csv_path=FIXTURE)


def test_out_of_range_after_latest_raises():
    """Date after the latest cached row raises ValueError, not silently
    returning the last-known rate."""
    with pytest.raises(ValueError, match="after the latest"):
        tbill.tbill_rate(date(2030, 1, 1), csv_path=FIXTURE)


def test_module_level_cache_reuses_load():
    """Calling tbill_rate twice with the same csv_path uses the cache
    (the file is parsed once, subsequent lookups hit the in-memory
    sorted list)."""
    tbill._loaded_cache.clear()
    assert FIXTURE not in tbill._loaded_cache
    tbill.tbill_rate(date(2025, 6, 16), csv_path=FIXTURE)
    assert FIXTURE in tbill._loaded_cache
    n_rows = len(tbill._loaded_cache[FIXTURE])
    tbill.tbill_rate(date(2025, 5, 27), csv_path=FIXTURE)
    # Cache still populated, same length (no re-load):
    assert len(tbill._loaded_cache[FIXTURE]) == n_rows
