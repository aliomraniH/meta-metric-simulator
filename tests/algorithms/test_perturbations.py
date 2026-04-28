"""Unit tests for algorithms/perturbations.py.

Per the M4c contract:
  1. Ramp hits the endpoint exactly at the completion day.
  2. Step changes the value instantly at the specified tick.
  3. Spike returns to baseline exactly at duration_days + 1.
  4. Determinism — no rng; same input → same output bit-for-bit.
"""
from __future__ import annotations

import copy

import pytest

from algorithms.perturbations import apply


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

def _state() -> dict:
    """Nested state bag exercising dotted-path target navigation."""
    return {
        "monetization": {"ad_load": 0.20},
        "ranking_weights": {"send": 1.0, "watch_time": 1.0},
        "integrity_dynamics": {"organic_decay_per_day": 0.02},
    }


# -----------------------------------------------------------------------------
# 1. Ramp
# -----------------------------------------------------------------------------

def test_ramp_hits_from_at_day_zero():
    pert = {"type": "ramp", "target": "monetization.ad_load",
            "from": 0.20, "to": 0.30, "days": 14}
    out = apply(_state(), pert, tick_day=0)
    assert out["monetization"]["ad_load"] == 0.20


def test_ramp_hits_to_exactly_at_completion_day():
    pert = {"type": "ramp", "target": "monetization.ad_load",
            "from": 0.20, "to": 0.30, "days": 14}
    out = apply(_state(), pert, tick_day=14)
    assert out["monetization"]["ad_load"] == 0.30


def test_ramp_linearly_interpolates_at_midpoint():
    pert = {"type": "ramp", "target": "monetization.ad_load",
            "from": 0.20, "to": 0.30, "days": 10}
    out = apply(_state(), pert, tick_day=5)
    assert abs(out["monetization"]["ad_load"] - 0.25) < 1e-12


def test_ramp_clamps_after_completion_day():
    pert = {"type": "ramp", "target": "monetization.ad_load",
            "from": 0.20, "to": 0.30, "days": 14}
    out = apply(_state(), pert, tick_day=999)
    assert out["monetization"]["ad_load"] == 0.30


def test_ramp_clamps_before_start_day():
    """Negative tick_day (shouldn't happen in practice) clamps to `from`."""
    pert = {"type": "ramp", "target": "monetization.ad_load",
            "from": 0.20, "to": 0.30, "days": 14}
    out = apply(_state(), pert, tick_day=-3)
    assert out["monetization"]["ad_load"] == 0.20


def test_ramp_zero_days_degenerates_to_to():
    pert = {"type": "ramp", "target": "monetization.ad_load",
            "from": 0.20, "to": 0.30, "days": 0}
    out = apply(_state(), pert, tick_day=5)
    assert out["monetization"]["ad_load"] == 0.30


def test_ramp_missing_field_raises():
    bad = {"type": "ramp", "target": "monetization.ad_load", "from": 0.2, "to": 0.3}
    with pytest.raises(ValueError):
        apply(_state(), bad, tick_day=0)


# -----------------------------------------------------------------------------
# 2. Step
# -----------------------------------------------------------------------------

def test_step_changes_value_instantly():
    pert = {"type": "step", "target": "monetization.ad_load", "value": 0.40}
    out = apply(_state(), pert, tick_day=0)
    assert out["monetization"]["ad_load"] == 0.40


def test_step_value_is_independent_of_tick_day():
    pert = {"type": "step", "target": "monetization.ad_load", "value": 0.40}
    for day in (0, 1, 100, 999):
        out = apply(_state(), pert, tick_day=day)
        assert out["monetization"]["ad_load"] == 0.40


def test_step_missing_value_raises():
    bad = {"type": "step", "target": "monetization.ad_load"}
    with pytest.raises(ValueError):
        apply(_state(), bad, tick_day=0)


# -----------------------------------------------------------------------------
# 3. Spike
# -----------------------------------------------------------------------------

def test_spike_active_at_start():
    pert = {"type": "spike", "target": "monetization.ad_load",
            "to": 0.50, "duration_days": 3, "baseline": 0.20}
    out = apply(_state(), pert, tick_day=0)
    assert out["monetization"]["ad_load"] == 0.50


def test_spike_active_through_duration_days():
    pert = {"type": "spike", "target": "monetization.ad_load",
            "to": 0.50, "duration_days": 3, "baseline": 0.20}
    for day in (0, 1, 2, 3):
        out = apply(_state(), pert, tick_day=day)
        assert out["monetization"]["ad_load"] == 0.50, f"failed at tick_day={day}"


def test_spike_returns_to_baseline_exactly_at_duration_plus_one():
    pert = {"type": "spike", "target": "monetization.ad_load",
            "to": 0.50, "duration_days": 3, "baseline": 0.20}
    out = apply(_state(), pert, tick_day=4)  # duration_days + 1
    assert out["monetization"]["ad_load"] == 0.20


def test_spike_remains_at_baseline_after_duration():
    pert = {"type": "spike", "target": "monetization.ad_load",
            "to": 0.50, "duration_days": 3, "baseline": 0.20}
    for day in (4, 5, 50, 1000):
        out = apply(_state(), pert, tick_day=day)
        assert out["monetization"]["ad_load"] == 0.20


def test_spike_without_baseline_falls_back_to_state_value():
    """When the engine forgot to stamp baseline, read the current value."""
    pert = {"type": "spike", "target": "monetization.ad_load",
            "to": 0.50, "duration_days": 3}
    out = apply(_state(), pert, tick_day=4)
    assert out["monetization"]["ad_load"] == 0.20


# -----------------------------------------------------------------------------
# 4. Determinism + invariants
# -----------------------------------------------------------------------------

def test_apply_is_bit_for_bit_deterministic():
    pert = {"type": "ramp", "target": "monetization.ad_load",
            "from": 0.20, "to": 0.30, "days": 14}
    a = apply(_state(), pert, tick_day=7)
    b = apply(_state(), pert, tick_day=7)
    assert a == b


def test_apply_does_not_mutate_input_state():
    state = _state()
    snapshot = copy.deepcopy(state)
    pert = {"type": "step", "target": "monetization.ad_load", "value": 0.99}
    apply(state, pert, tick_day=0)
    assert state == snapshot


def test_apply_does_not_mutate_perturbation_def():
    state = _state()
    pert = {"type": "spike", "target": "monetization.ad_load",
            "to": 0.50, "duration_days": 3, "baseline": 0.20}
    snap = copy.deepcopy(pert)
    apply(state, pert, tick_day=2)
    assert pert == snap


# -----------------------------------------------------------------------------
# Edge cases
# -----------------------------------------------------------------------------

def test_unknown_perturbation_type_raises():
    with pytest.raises(ValueError):
        apply(_state(), {"type": "wiggle", "target": "monetization.ad_load"}, 0)


def test_unknown_target_path_raises():
    pert = {"type": "step", "target": "no.such.thing", "value": 1.0}
    with pytest.raises(KeyError):
        apply(_state(), pert, tick_day=0)


def test_target_can_navigate_arbitrary_dotted_paths():
    pert = {"type": "step", "target": "ranking_weights.send", "value": 5.0}
    out = apply(_state(), pert, tick_day=0)
    assert out["ranking_weights"]["send"] == 5.0
    # Other paths untouched.
    assert out["ranking_weights"]["watch_time"] == 1.0
    assert out["monetization"]["ad_load"] == 0.20
