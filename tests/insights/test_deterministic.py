"""Tests for insights/deterministic.py.

These tests use a tiny in-test ``_FakeRuntime`` that satisfies the
``compute(metric_name, scenario_id, group_by) -> list[dict]`` contract
the real ``MetricRuntime`` implements (per metrics/runtime.py).  This
keeps the deterministic-detector tests fast and reproducible — building
a real M7 scenario per test would cost ~2 s/test for behaviour that is
already exhaustively exercised in tests/metrics/test_runtime.py.

The detectors themselves only consume `compute()`; substituting a fake
that returns canned `[{"dim": tick, "value": v}, ...]` rows tests the
full anomaly + rank logic without binding to simulator output.
"""
from __future__ import annotations

import pytest

from insights.deterministic import (
    Anomaly,
    metric_rank,
    percent_diff,
    z_score_anomalies,
)


# ---------------------------------------------------------------------------
# Fake metric runtime
# ---------------------------------------------------------------------------

class _FakeRuntime:
    """In-test stand-in for MetricRuntime.

    Holds a `{(metric_name, scenario_id): [(tick, value), ...]}` table
    and serves it through the same `.compute()` async API."""

    def __init__(self):
        self.table: dict[tuple[str, str], list[tuple[int, float | None]]] = {}

    def set_series(
        self, metric_name: str, scenario_id: str, series: list[tuple[int, float | None]]
    ) -> None:
        self.table[(metric_name, scenario_id)] = list(series)

    async def compute(
        self,
        metric_name: str,
        scenario_id: str,
        group_by: str | None = None,
        **_: object,
    ) -> list[dict[str, object]]:
        series = self.table.get((metric_name, scenario_id), [])
        return [{"dim": tick, "value": value} for tick, value in series]


# ---------------------------------------------------------------------------
# z_score_anomalies
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_z_score_detects_synthetic_outlier():
    """14 ticks of stationary mean=100 stddev=5, then tick 14 = 200 → strong above-mean anomaly."""
    rt = _FakeRuntime()
    series = [
        (0, 95.0), (1, 105.0), (2, 100.0), (3, 102.0), (4, 98.0),
        (5, 101.0), (6, 99.0), (7, 103.0), (8, 97.0), (9, 100.0),
        (10, 105.0), (11, 95.0), (12, 102.0), (13, 98.0),
        (14, 200.0),  # outlier
    ]
    rt.set_series("test_metric", "scn-A", series)
    anomalies = await z_score_anomalies(
        scenario_id="scn-A",
        metric_name="test_metric",
        metric_runtime=rt,
        window=10,
        threshold=2.5,
    )
    assert len(anomalies) == 1
    a = anomalies[0]
    assert a.tick_day == 14
    assert a.direction == "above"
    assert a.z_score > 2.5
    assert a.observed_value == 200.0


@pytest.mark.asyncio
async def test_z_score_no_anomalies_in_stationary_series():
    """Constant-ish values around mean 100 stddev ~5 → no anomalies at threshold 2.5."""
    rt = _FakeRuntime()
    series = [
        (i, 100.0 + (i % 3) - 1) for i in range(30)  # values in {99, 100, 101}
    ]
    rt.set_series("stationary", "scn-B", series)
    anomalies = await z_score_anomalies(
        scenario_id="scn-B",
        metric_name="stationary",
        metric_runtime=rt,
        window=14,
        threshold=2.5,
    )
    assert anomalies == []


@pytest.mark.asyncio
async def test_z_score_skips_short_history():
    """Series shorter than the window → no anomalies (insufficient history)."""
    rt = _FakeRuntime()
    rt.set_series("short", "scn-C", [(i, float(i)) for i in range(5)])
    anomalies = await z_score_anomalies(
        scenario_id="scn-C",
        metric_name="short",
        metric_runtime=rt,
        window=14,
        threshold=2.5,
    )
    assert anomalies == []


@pytest.mark.asyncio
async def test_z_score_skips_zero_stddev_window():
    """Window has constant values → stddev=0 → must skip without ZeroDivisionError."""
    rt = _FakeRuntime()
    series = [(i, 50.0) for i in range(14)] + [(14, 100.0)]
    rt.set_series("constant_window", "scn-D", series)
    anomalies = await z_score_anomalies(
        scenario_id="scn-D",
        metric_name="constant_window",
        metric_runtime=rt,
        window=14,
        threshold=2.5,
    )
    # The constant-window tick produced no anomaly (skipped); no exception.
    # Subsequent ticks would also have window stddev=0 because the new value
    # only enters the window once it's history.
    assert anomalies == []


# ---------------------------------------------------------------------------
# percent_diff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_percent_diff_symmetric():
    """Swapping scenario order flips the sign but preserves magnitude."""
    rt = _FakeRuntime()
    rt.set_series("rev", "A", [(0, 100.0)])
    rt.set_series("rev", "B", [(0, 50.0)])
    forward = await percent_diff("A", "B", "rev", rt)
    backward = await percent_diff("B", "A", "rev", rt)
    assert forward["pct_diff"] == pytest.approx(-backward["pct_diff"])
    assert abs(forward["pct_diff"]) == pytest.approx(abs(backward["pct_diff"]))


@pytest.mark.asyncio
async def test_percent_diff_handles_zero():
    """One zero, one non-zero → ±2.0 (max divergence)."""
    rt = _FakeRuntime()
    rt.set_series("rev", "A", [(0, 0.0)])
    rt.set_series("rev", "B", [(0, 10.0)])
    out = await percent_diff("A", "B", "rev", rt)
    assert out["pct_diff"] == pytest.approx(-2.0)
    out_swap = await percent_diff("B", "A", "rev", rt)
    assert out_swap["pct_diff"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_percent_diff_handles_both_zero():
    rt = _FakeRuntime()
    rt.set_series("rev", "A", [(0, 0.0)])
    rt.set_series("rev", "B", [(0, 0.0)])
    out = await percent_diff("A", "B", "rev", rt)
    assert out["pct_diff"] == 0.0
    assert out["abs_diff"] == 0.0


@pytest.mark.asyncio
async def test_percent_diff_uses_final_tick():
    """Multiple ticks present → pct_diff uses the final non-None value."""
    rt = _FakeRuntime()
    rt.set_series("rev", "A", [(0, 50.0), (1, 80.0), (2, 100.0)])
    rt.set_series("rev", "B", [(0, 100.0), (1, 80.0), (2, 50.0)])
    out = await percent_diff("A", "B", "rev", rt)
    # 2*(100-50)/(100+50) = 100/150 = 2/3
    assert out["pct_diff"] == pytest.approx(2.0 / 3.0)
    assert out["value_a"] == 100.0
    assert out["value_b"] == 50.0


# ---------------------------------------------------------------------------
# metric_rank
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_metric_rank_basic():
    rt = _FakeRuntime()
    rt.set_series("rev", "X", [(0, 100.0)])
    rt.set_series("rev", "Y", [(0, 200.0)])
    rt.set_series("rev", "Z", [(0, 50.0)])
    ranks = await metric_rank(["X", "Y", "Z"], "rev", rt)
    by_id = {sid: rk for sid, _, rk in ranks}
    assert by_id["Y"] == 1  # 200 → rank 1
    assert by_id["X"] == 2  # 100 → rank 2
    assert by_id["Z"] == 3  # 50  → rank 3


@pytest.mark.asyncio
async def test_metric_rank_handles_ties():
    """Two scenarios tied at 100 → both get rank 1; the lone 50 gets rank 3."""
    rt = _FakeRuntime()
    rt.set_series("rev", "X", [(0, 100.0)])
    rt.set_series("rev", "Y", [(0, 100.0)])
    rt.set_series("rev", "Z", [(0, 50.0)])
    ranks = await metric_rank(["X", "Y", "Z"], "rev", rt)
    by_id = {sid: rk for sid, _, rk in ranks}
    assert by_id["X"] == 1
    assert by_id["Y"] == 1
    assert by_id["Z"] == 3


@pytest.mark.asyncio
async def test_metric_rank_stable():
    """Same input → same output across two runs (no rng)."""
    rt = _FakeRuntime()
    rt.set_series("rev", "X", [(0, 100.0)])
    rt.set_series("rev", "Y", [(0, 200.0)])
    rt.set_series("rev", "Z", [(0, 50.0)])
    a = await metric_rank(["X", "Y", "Z"], "rev", rt)
    b = await metric_rank(["X", "Y", "Z"], "rev", rt)
    assert a == b


# ---------------------------------------------------------------------------
# Anomaly representation
# ---------------------------------------------------------------------------

def test_anomaly_str_repr():
    a = Anomaly(
        scenario_id="scn-A",
        metric_name="ad_revenue_per_dau",
        tick_day=14,
        observed_value=0.1234,
        expected_value=0.0500,
        z_score=3.4567,
        direction="above",
    )
    s = str(a)
    assert "ad_revenue_per_dau" in s
    assert "day 14" in s
    # 2-decimal z-score formatting.
    assert "z=3.46" in s
    assert "above" in s
