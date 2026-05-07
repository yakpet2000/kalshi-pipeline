"""Unit tests for the runtime sanity checks in scripts/simulate_test_b.py.

Both checks were added in sub-stage 2b.7 specifically to catch the
class of bug that produced the original failed-and-discarded run:

(a) preflight_schema_check: would have caught the original bug
    (assuming `settlement_value_dollars` was in candidate-universe.csv
    when it was not) at the earliest possible point, by failing on
    the missing key before the simulator started.

(b) sanity_check_voided_count: catches a broader class of failures
    where input keys exist but produce garbage classifications.
    Compares against the 2b.5/2b.2.1 audit (0 voided in the universe).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "simulate_test_b", PROJECT_ROOT / "scripts" / "simulate_test_b.py"
)
sim = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sim)


# ---------------------------------------------------------------------------
# preflight_schema_check
# ---------------------------------------------------------------------------


def _write_payload(tmpdir: Path, ticker: str, market: dict) -> None:
    p = tmpdir / f"{ticker}.json"
    import json as _json
    p.write_text(_json.dumps({"market": market}))


def test_preflight_schema_check_passes_with_complete_payload(tmp_path, monkeypatch):
    """All required keys present -> no exception."""
    monkeypatch.setattr(sim, "MARKET_DETAILS_DIR", tmp_path)
    _write_payload(tmp_path, "T1", {
        "result": "yes",
        "settlement_value_dollars": "1.0000",
        "settlement_ts": "2026-04-01T00:00:00Z",
        "expected_expiration_time": "2026-04-01T00:00:00Z",
        "expiration_time": "2026-04-01T00:00:00Z",
        "status": "finalized",
    })
    sim.preflight_schema_check(["T1"])  # should not raise


def test_preflight_schema_check_fails_on_missing_settlement_value_dollars(
    tmp_path, monkeypatch
):
    """The exact bug from the original 2b.7 run: settlement_value_dollars
    absent. Schema check catches it before the simulator starts."""
    monkeypatch.setattr(sim, "MARKET_DETAILS_DIR", tmp_path)
    _write_payload(tmp_path, "T1", {
        "result": "yes",
        # NO settlement_value_dollars
        "settlement_ts": "2026-04-01T00:00:00Z",
        "expected_expiration_time": "2026-04-01T00:00:00Z",
        "expiration_time": "2026-04-01T00:00:00Z",
        "status": "finalized",
    })
    with pytest.raises(sim.SchemaCheckFailure, match="missing required keys"):
        sim.preflight_schema_check(["T1"])


def test_preflight_schema_check_fails_on_missing_result(tmp_path, monkeypatch):
    """Missing the `result` key — the other half of the void OR rule."""
    monkeypatch.setattr(sim, "MARKET_DETAILS_DIR", tmp_path)
    _write_payload(tmp_path, "T1", {
        # NO result
        "settlement_value_dollars": "0.0000",
        "settlement_ts": "2026-04-01T00:00:00Z",
        "expected_expiration_time": "2026-04-01T00:00:00Z",
        "expiration_time": "2026-04-01T00:00:00Z",
        "status": "finalized",
    })
    with pytest.raises(sim.SchemaCheckFailure, match="missing required keys"):
        sim.preflight_schema_check(["T1"])


def test_preflight_schema_check_fails_on_missing_void_proxy_keys(
    tmp_path, monkeypatch
):
    """settlement_ts and expected_expiration_time are conditional
    (only used for voided markets) but the schema check still
    requires them as keys so a future voided market doesn't fail
    mid-simulation."""
    monkeypatch.setattr(sim, "MARKET_DETAILS_DIR", tmp_path)
    _write_payload(tmp_path, "T1", {
        "result": "yes",
        "settlement_value_dollars": "1.0000",
        # NO settlement_ts or expected_expiration_time
        "status": "finalized",
    })
    with pytest.raises(sim.SchemaCheckFailure, match="missing required keys"):
        sim.preflight_schema_check(["T1"])


def test_preflight_schema_check_fails_on_missing_file(tmp_path, monkeypatch):
    """If a cache file is missing entirely, raise SchemaCheckFailure
    with a clear message pointing at the fetcher script."""
    monkeypatch.setattr(sim, "MARKET_DETAILS_DIR", tmp_path)
    # Don't write any payload
    with pytest.raises(sim.SchemaCheckFailure, match="missing market-details cache"):
        sim.preflight_schema_check(["T1"])


def test_preflight_schema_check_payload_with_no_market_subdict(
    tmp_path, monkeypatch
):
    """A payload with no 'market' sub-dict raises with a clear message."""
    import json as _json
    monkeypatch.setattr(sim, "MARKET_DETAILS_DIR", tmp_path)
    (tmp_path / "T1.json").write_text(_json.dumps({"unexpected": "shape"}))
    with pytest.raises(sim.SchemaCheckFailure, match="missing required keys"):
        sim.preflight_schema_check(["T1"])


def test_preflight_schema_check_reports_missing_keys_count(tmp_path, monkeypatch):
    """When multiple tickers fail, the error message reports the
    count and a sample. (Defensive: the original bug would have
    failed all 98 universe tickers simultaneously.)"""
    monkeypatch.setattr(sim, "MARKET_DETAILS_DIR", tmp_path)
    for t in ("T1", "T2", "T3"):
        _write_payload(tmp_path, t, {"status": "finalized"})  # missing many keys
    with pytest.raises(sim.SchemaCheckFailure) as exc_info:
        sim.preflight_schema_check(["T1", "T2", "T3"])
    assert "3 ticker(s)" in str(exc_info.value)


# ---------------------------------------------------------------------------
# sanity_check_voided_count
# ---------------------------------------------------------------------------


def test_voided_count_sanity_passes_when_zero():
    """voided_count = 0 is the locked expected value per the 2b.5
    universe audit and 2b.2.1 re-verification."""
    sim.sanity_check_voided_count({"voided_count": 0})  # no exception


def test_voided_count_sanity_fails_when_nonzero():
    """voided_count != 0 contradicts the 2b.5 audit. Refuses to
    treat the run as valid."""
    with pytest.raises(sim.VoidedCountSanityFailure, match="voided_count=5"):
        sim.sanity_check_voided_count({"voided_count": 5})


def test_voided_count_sanity_fails_when_all_marked_voided():
    """The exact failure mode from the original 2b.7 run: every
    filled position misclassified as voided."""
    with pytest.raises(sim.VoidedCountSanityFailure, match="voided_count=102"):
        sim.sanity_check_voided_count({"voided_count": 102})


def test_voided_count_sanity_fails_when_key_missing():
    """If diagnostics dict is malformed and lacks voided_count, the
    sentinel default (-1) trips the check."""
    with pytest.raises(sim.VoidedCountSanityFailure, match="voided_count=-1"):
        sim.sanity_check_voided_count({})


def test_classify_settlement_yes():
    """Smoke: result=yes, sv=1.0000 -> 'yes'."""
    market = {"status": "finalized", "result": "yes",
              "settlement_value_dollars": "1.0000"}
    assert sim.classify_settlement(market) == "yes"


def test_classify_settlement_no():
    market = {"status": "finalized", "result": "no",
              "settlement_value_dollars": "0.0000"}
    assert sim.classify_settlement(market) == "no"


def test_classify_settlement_voided_via_empty_result():
    market = {"status": "finalized", "result": "",
              "settlement_value_dollars": "1.0000"}
    assert sim.classify_settlement(market) == "voided"


def test_classify_settlement_voided_via_unusual_sv():
    market = {"status": "finalized", "result": "yes",
              "settlement_value_dollars": "0.5000"}
    assert sim.classify_settlement(market) == "voided"


def test_classify_settlement_raises_on_non_finalized_status():
    """Universe markets are filtered to status=finalized; anything
    else is a data inconsistency and should not silently classify."""
    market = {"status": "active", "result": "",
              "settlement_value_dollars": ""}
    with pytest.raises(ValueError, match="non-finalized status"):
        sim.classify_settlement(market)
