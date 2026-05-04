"""Layer 8 deterministic detectors — agent-free per PDF §3.6.

Three pure async functions over the metrics layer:

  z_score_anomalies — rolling-window z-score over per-tick metrics.
  percent_diff      — symmetric percent diff between two scenarios.
  metric_rank       — stable ranks across N scenarios.

Architectural rule (AGENTIC_ARCHITECTURE_INDEX.md §1 / PDF §3.6):
deterministic detectors compute the anomalies; the M16 narrator
describes them in prose.  Letting the narrator invent numbers means
the narrator hallucinates.  Every output of this module is a
structured object with explicit numeric values that a narrator can
only quote, never invent.

Layer-import discipline:
  insights/ MAY import from metrics/, substrate/, models/, stdlib,
  dataclasses, scipy.
  insights/ MAY NOT import from algorithms/, engine/, params/,
  baselines/, calibration/, interview/, tools/.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from scipy.stats import rankdata


@dataclass
class Anomaly:
    """One above- or below-window anomaly at a specific tick."""

    scenario_id: str
    metric_name: str
    tick_day: int
    observed_value: float
    expected_value: float  # rolling-window mean
    z_score: float
    direction: str  # "above" | "below"

    def __str__(self) -> str:
        return (
            f"{self.metric_name} at day {self.tick_day}: "
            f"{self.observed_value:.4f} (z={self.z_score:.2f}, "
            f"{self.direction} window mean {self.expected_value:.4f})"
        )


# ---------------------------------------------------------------------------
# Protocol — duck-type the small slice of MetricRuntime we actually need
# ---------------------------------------------------------------------------

class _MetricRuntimeLike(Protocol):
    async def compute(
        self,
        metric_name: str,
        scenario_id: str,
        group_by: str | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        ...


# ---------------------------------------------------------------------------
# z_score_anomalies — rolling window
# ---------------------------------------------------------------------------

async def z_score_anomalies(
    scenario_id: str,
    metric_name: str,
    metric_runtime: _MetricRuntimeLike,
    window: int = 14,
    threshold: float = 2.5,
) -> list[Anomaly]:
    """Detect anomalies via look-back rolling z-score.

    For each tick t with at least `window` prior observations, compute
    the mean + sample stddev over [t-window, t-1] (look-back, not
    centered — the simulator runs forward in time, so a centered
    window would peek at the future).  If `abs((observed - mean) /
    stddev) >= threshold`, emit an Anomaly.

    Args:
        scenario_id:    canonical scenario id.
        metric_name:    metric defined under metrics/definitions/.
        metric_runtime: runtime providing async `.compute()`.
        window:         look-back length in ticks (default 14).
        threshold:      |z| ≥ this is an anomaly (default 2.5).

    Returns:
        List of Anomaly sorted by tick_day ascending.  Empty list on
        insufficient history or fully constant series.
    """
    rows = await metric_runtime.compute(
        metric_name, scenario_id=scenario_id, group_by="tick_day"
    )
    series = _rows_to_series(rows)
    if not series:
        return []

    out: list[Anomaly] = []
    for i, (tick_day, observed) in enumerate(series):
        if i < window:
            continue
        if observed is None:
            continue
        history = [v for _, v in series[i - window:i] if v is not None]
        if len(history) < 2:
            continue
        mean, stddev = _mean_stddev(history)
        if stddev == 0.0:
            continue
        z = (observed - mean) / stddev
        if abs(z) < threshold:
            continue
        out.append(Anomaly(
            scenario_id=scenario_id,
            metric_name=metric_name,
            tick_day=int(tick_day),
            observed_value=float(observed),
            expected_value=float(mean),
            z_score=float(z),
            direction="above" if z > 0 else "below",
        ))
    return out


# ---------------------------------------------------------------------------
# percent_diff — symmetric, between two scenarios at final tick
# ---------------------------------------------------------------------------

async def percent_diff(
    scenario_a_id: str,
    scenario_b_id: str,
    metric_name: str,
    metric_runtime: _MetricRuntimeLike,
) -> dict[str, Any]:
    """Symmetric percent diff between two scenarios for one metric.

    Uses the final tick of each scenario (the last tick_day that has
    a non-None value).  Symmetric form: `pct_diff = 2 * (a - b) / (a + b)`,
    range `[-2, 2]`.  Sign is positive when scenario_a > scenario_b.

    Special cases:
      - Both values 0 → pct_diff = 0.0.
      - Exactly one value 0 → pct_diff = ±2.0 (max divergence; sign
        follows the non-zero value).
    """
    value_a = await _final_tick_value(scenario_a_id, metric_name, metric_runtime)
    value_b = await _final_tick_value(scenario_b_id, metric_name, metric_runtime)

    abs_diff = (value_a or 0.0) - (value_b or 0.0)
    if (value_a or 0.0) == 0.0 and (value_b or 0.0) == 0.0:
        pct_diff = 0.0
    elif (value_a or 0.0) == 0.0 or (value_b or 0.0) == 0.0:
        # max divergence; sign follows the non-zero side
        pct_diff = 2.0 if (value_a or 0.0) > (value_b or 0.0) else -2.0
    else:
        denom = (value_a + value_b)
        # denom can only be zero when a == -b ≠ 0; treat as max divergence.
        pct_diff = 2.0 * (value_a - value_b) / denom if denom != 0 else (
            2.0 if value_a > value_b else -2.0
        )

    return {
        "scenario_a_id": scenario_a_id,
        "scenario_b_id": scenario_b_id,
        "metric_name": metric_name,
        "value_a": value_a,
        "value_b": value_b,
        "pct_diff": pct_diff,
        "abs_diff": abs_diff,
    }


# ---------------------------------------------------------------------------
# metric_rank — stable ranks across N scenarios
# ---------------------------------------------------------------------------

async def metric_rank(
    scenario_ids: list[str],
    metric_name: str,
    metric_runtime: _MetricRuntimeLike,
) -> list[tuple[str, float, int]]:
    """Rank scenarios by final-tick metric value, descending.

    Rank 1 = highest value.  Ties get the same rank via
    `scipy.stats.rankdata(..., method="min")` so a tied first-place pair
    both get rank 1 (and the next distinct value is rank 3).

    Returns:
        List of `(scenario_id, value, rank)` ordered by rank ascending,
        ties broken by the input order (stable).
    """
    values: list[float] = []
    for sid in scenario_ids:
        v = await _final_tick_value(sid, metric_name, metric_runtime)
        values.append(v if v is not None else 0.0)

    if not values:
        return []

    # rankdata gives ascending ranks; we want descending → negate.
    ranks = rankdata([-v for v in values], method="min").astype(int).tolist()

    # Pair up + sort by (rank, input order) for a stable result.
    paired = list(zip(scenario_ids, values, ranks, range(len(scenario_ids))))
    paired.sort(key=lambda t: (t[2], t[3]))
    return [(sid, val, rk) for sid, val, rk, _ in paired]


# ---------------------------------------------------------------------------
# Helpers — pure
# ---------------------------------------------------------------------------

def _rows_to_series(rows: Iterable[dict[str, Any]]) -> list[tuple[int, float | None]]:
    """Convert metric runtime rows into [(tick_day, value)] sorted by tick_day."""
    series: list[tuple[int, float | None]] = []
    for r in rows:
        dim = r.get("dim")
        val = r.get("value")
        if dim is None:
            continue
        try:
            tick = int(dim)
        except (TypeError, ValueError):
            continue
        series.append((tick, float(val) if val is not None else None))
    series.sort(key=lambda t: t[0])
    return series


def _mean_stddev(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n == 1:
        return mean, 0.0
    var = sum((v - mean) ** 2 for v in values) / (n - 1)  # sample stddev
    return mean, math.sqrt(var)


async def _final_tick_value(
    scenario_id: str,
    metric_name: str,
    metric_runtime: _MetricRuntimeLike,
) -> float:
    """Return the value at the largest tick_day with a non-None value, else 0.0."""
    rows = await metric_runtime.compute(
        metric_name, scenario_id=scenario_id, group_by="tick_day"
    )
    series = _rows_to_series(rows)
    for tick, val in reversed(series):
        if val is not None:
            return float(val)
    return 0.0
