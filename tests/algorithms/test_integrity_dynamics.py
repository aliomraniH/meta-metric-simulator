"""Unit tests for algorithms/integrity_dynamics.py.

Per the M4b contract:
  1. Prevalence respects the [floor, ceiling] bounds under decay + spike.
  2. Organic decay reduces prevalence over time when no perturbation fires.
  3. Spike perturbation raises prevalence to the target value at the
     correct tick.
  4. Determinism — no rng involved; same input → same output bit-for-bit.
"""
from __future__ import annotations

import copy

from algorithms.integrity_dynamics import evolve


def _params(decay: float = 0.05, detection_growth: float = 0.0) -> dict:
    return {
        "organic_decay_per_day": decay,
        "detection_growth_per_day": detection_growth,
        "floor": 0.0,
        "ceiling": 1.0,
    }


# -----------------------------------------------------------------------------
# 1. Bounds respected
# -----------------------------------------------------------------------------

def test_prevalence_clamped_to_ceiling():
    state = {"violating_prevalence": 0.99, "detection_rate": 0.5, "by_topic": {}}
    pert = [{"target": "violating_prevalence", "op": "set", "value": 5.0}]
    out = evolve(state, pert, _params())
    assert out["violating_prevalence"] == 1.0


def test_prevalence_clamped_to_floor():
    state = {"violating_prevalence": 0.01, "detection_rate": 0.5, "by_topic": {}}
    pert = [{"target": "violating_prevalence", "op": "set", "value": -1.0}]
    out = evolve(state, pert, _params())
    assert out["violating_prevalence"] == 0.0


def test_per_topic_prevalence_clamped_too():
    state = {
        "violating_prevalence": 0.10,
        "detection_rate": 0.5,
        "by_topic": {"adult": 0.95, "violence": 0.20},
    }
    pert = [{"target": "by_topic.adult", "op": "set", "value": 5.0}]
    out = evolve(state, pert, _params())
    assert out["by_topic"]["adult"] == 1.0
    assert 0.0 <= out["by_topic"]["violence"] <= 1.0


# -----------------------------------------------------------------------------
# 2. Organic decay reduces prevalence over time
# -----------------------------------------------------------------------------

def test_organic_decay_reduces_prevalence_each_tick():
    state = {"violating_prevalence": 0.10, "detection_rate": 0.5, "by_topic": {}}
    params = _params(decay=0.05)
    history: list[float] = [state["violating_prevalence"]]
    for _ in range(10):
        state = evolve(state, [], params)
        history.append(state["violating_prevalence"])
    # Strictly monotone decreasing.
    for prev, nxt in zip(history, history[1:]):
        assert nxt < prev
    # After 10 days at 5% decay, expect (1 - 0.05)**10 ≈ 0.5987.
    assert abs(history[-1] - 0.10 * (0.95 ** 10)) < 1e-9


def test_decay_zero_is_a_no_op():
    state = {"violating_prevalence": 0.10, "detection_rate": 0.5, "by_topic": {}}
    out = evolve(state, [], _params(decay=0.0))
    assert out["violating_prevalence"] == 0.10


# -----------------------------------------------------------------------------
# 3. Spike perturbation raises prevalence to target value
# -----------------------------------------------------------------------------

def test_spike_perturbation_sets_prevalence():
    """A spike applied at tick T sets prevalence to its target value
    AFTER organic decay (the operation order: decay → perturbations → clamp)."""
    state = {"violating_prevalence": 0.05, "detection_rate": 0.5, "by_topic": {}}
    pert = [{"target": "violating_prevalence", "op": "set", "value": 0.30}]
    out = evolve(state, pert, _params())
    # Op `set` overrides whatever decay produced.
    assert out["violating_prevalence"] == 0.30


def test_spike_with_op_add_increments_prevalence():
    state = {"violating_prevalence": 0.10, "detection_rate": 0.5, "by_topic": {}}
    pert = [{"target": "violating_prevalence", "op": "add", "value": 0.05}]
    out = evolve(state, pert, _params(decay=0.0))
    assert abs(out["violating_prevalence"] - 0.15) < 1e-9


def test_spike_target_per_topic():
    state = {
        "violating_prevalence": 0.10,
        "detection_rate": 0.5,
        "by_topic": {"misinformation": 0.04},
    }
    pert = [{"target": "by_topic.misinformation", "op": "set", "value": 0.20}]
    out = evolve(state, pert, _params(decay=0.0))
    assert out["by_topic"]["misinformation"] == 0.20


def test_unknown_perturbation_target_silently_ignored():
    state = {"violating_prevalence": 0.10, "detection_rate": 0.5, "by_topic": {}}
    pert = [{"target": "fictional_metric", "op": "set", "value": 0.99}]
    out = evolve(state, pert, _params(decay=0.0))
    # State unchanged for the legitimate fields.
    assert out["violating_prevalence"] == 0.10


def test_multiple_perturbations_apply_in_input_order():
    state = {"violating_prevalence": 0.05, "detection_rate": 0.5, "by_topic": {}}
    pert = [
        {"target": "violating_prevalence", "op": "set", "value": 0.30},
        {"target": "violating_prevalence", "op": "add", "value": 0.10},
    ]
    out = evolve(state, pert, _params(decay=0.0))
    assert abs(out["violating_prevalence"] - 0.40) < 1e-9


# -----------------------------------------------------------------------------
# 4. Determinism
# -----------------------------------------------------------------------------

def test_evolve_is_bit_for_bit_deterministic():
    state = {
        "violating_prevalence": 0.123,
        "detection_rate": 0.456,
        "by_topic": {"music": 0.05, "news": 0.08},
    }
    pert = [
        {"target": "violating_prevalence", "op": "add", "value": 0.01},
        {"target": "by_topic.news", "op": "multiply", "value": 1.5},
    ]
    a = evolve(state, pert, _params(decay=0.03, detection_growth=0.01))
    b = evolve(state, pert, _params(decay=0.03, detection_growth=0.01))
    assert a == b


def test_evolve_does_not_mutate_inputs():
    state = {
        "violating_prevalence": 0.10,
        "detection_rate": 0.5,
        "by_topic": {"music": 0.05},
    }
    pert = [{"target": "by_topic.music", "op": "set", "value": 0.50}]
    snap_state = copy.deepcopy(state)
    snap_pert = copy.deepcopy(pert)
    evolve(state, pert, _params())
    assert state == snap_state
    assert pert == snap_pert


# -----------------------------------------------------------------------------
# Detection rate growth
# -----------------------------------------------------------------------------

def test_detection_rate_drifts_toward_one():
    state = {"violating_prevalence": 0.10, "detection_rate": 0.50, "by_topic": {}}
    out = evolve(state, [], _params(decay=0.0, detection_growth=0.10))
    # detection grows by 0.10 * (1 - 0.50) = 0.05.
    assert abs(out["detection_rate"] - 0.55) < 1e-9


def test_detection_rate_clamped_to_one():
    state = {"violating_prevalence": 0.10, "detection_rate": 0.99, "by_topic": {}}
    out = evolve(state, [], _params(decay=0.0, detection_growth=10.0))
    assert out["detection_rate"] == 1.0
