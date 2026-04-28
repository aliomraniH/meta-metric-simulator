"""Unit tests for algorithms/guardrails.py.

Per the M4a contract:
  1. One test per breaker — fires when the threshold is crossed.
  2. Doesn't fire when below threshold.
  3. Severity levels (warn / breaker / halt) are emitted correctly.
  4. Output dicts pass the substrate writer's structural validation
     (the `_prepare` function in substrate/writer.py).
"""
from __future__ import annotations

import pytest

from algorithms.guardrails import GUARDRAIL_KINDS, check
from substrate.writer import EventValidationError, _prepare


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _params() -> dict:
    """Threshold params with all four breakers configured.

    Ceilings (direction=above) for prevalence, teen_exposure, ad_load.
    Floor (direction=below) for creator_supply_collapse.
    """
    return {
        "integrity_prevalence": {
            "warn": 0.012, "breaker": 0.015, "halt": 0.020, "direction": "above",
        },
        "teen_exposure": {
            "warn": 0.030, "breaker": 0.045, "halt": 0.060, "direction": "above",
        },
        "creator_supply_collapse": {
            "warn": 0.80, "breaker": 0.65, "halt": 0.50, "direction": "below",
        },
        "ad_load_ceiling": {
            "warn": 0.22, "breaker": 0.27, "halt": 0.32, "direction": "above",
        },
    }


def _tick_state(**overrides) -> dict:
    base: dict = {
        "scenario_id": "scn-test",
        "tick_day": 3,
        "intra_day_seq": 1,
        "timestamp": 12345.0,
        "prevalence_state": {"violating_prevalence": 0.005, "detection_rate": 0.6},
        "segment_state_map": {
            "teen": {"integrity_exposure": 0.010},
            "young_adult": {"integrity_exposure": 0.008},
        },
        "creator_supply": 1.00,
        "ad_load_observed": 0.18,
    }
    base.update(overrides)
    return base


def _kinds_in(events: list[dict]) -> set[str]:
    return {e["payload"]["guardrail_kind"] for e in events}


def _by_kind(events: list[dict], kind: str) -> dict:
    matches = [e for e in events if e["payload"]["guardrail_kind"] == kind]
    assert len(matches) <= 1, f"more than one event for kind {kind}"
    return matches[0] if matches else {}


# -----------------------------------------------------------------------------
# 1. Each breaker fires when its threshold is crossed
# -----------------------------------------------------------------------------

def test_integrity_prevalence_fires_when_above_warn():
    state = _tick_state(prevalence_state={"violating_prevalence": 0.013, "detection_rate": 0.6})
    events = check(state, recent_events=[], params=_params())
    assert "integrity_prevalence" in _kinds_in(events)
    ev = _by_kind(events, "integrity_prevalence")
    assert ev["payload"]["severity"] == "warn"
    assert ev["payload"]["observed"] == 0.013
    assert ev["payload"]["threshold"] == 0.012


def test_teen_exposure_fires_when_above_breaker():
    state = _tick_state(
        segment_state_map={"teen": {"integrity_exposure": 0.050}},
    )
    events = check(state, recent_events=[], params=_params())
    assert "teen_exposure" in _kinds_in(events)
    ev = _by_kind(events, "teen_exposure")
    assert ev["payload"]["severity"] == "breaker"
    assert ev["viewer_segment"] == "teen"


def test_creator_supply_collapse_fires_when_below_floor():
    state = _tick_state(creator_supply=0.55)
    events = check(state, recent_events=[], params=_params())
    assert "creator_supply_collapse" in _kinds_in(events)
    ev = _by_kind(events, "creator_supply_collapse")
    # 0.55 < warn(0.80) and < breaker(0.65), but ≥ halt(0.50) → severity=breaker.
    assert ev["payload"]["severity"] == "breaker"
    assert ev["payload"]["direction"] == "below"


def test_ad_load_ceiling_fires_when_above_halt():
    state = _tick_state(ad_load_observed=0.40)
    events = check(state, recent_events=[], params=_params())
    assert "ad_load_ceiling" in _kinds_in(events)
    ev = _by_kind(events, "ad_load_ceiling")
    # 0.40 > halt(0.32) → severity=halt (highest crossed).
    assert ev["payload"]["severity"] == "halt"


# -----------------------------------------------------------------------------
# 2. Doesn't fire when below threshold
# -----------------------------------------------------------------------------

def test_no_events_when_all_below_thresholds():
    """Default _tick_state has every value safely below all thresholds."""
    events = check(_tick_state(), recent_events=[], params=_params())
    assert events == []


def test_only_the_crossing_kind_fires():
    """Crossing one threshold doesn't fire the other three breakers."""
    state = _tick_state(ad_load_observed=0.25)  # > warn(0.22), < breaker(0.27)
    events = check(state, recent_events=[], params=_params())
    assert _kinds_in(events) == {"ad_load_ceiling"}


# -----------------------------------------------------------------------------
# 3. Severity levels emitted correctly
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "observed, expected_severity",
    [
        (0.013, "warn"),
        (0.016, "breaker"),
        (0.025, "halt"),
    ],
)
def test_severity_picks_highest_crossed_for_ceiling(observed, expected_severity):
    state = _tick_state(prevalence_state={"violating_prevalence": observed})
    events = check(state, recent_events=[], params=_params())
    assert _by_kind(events, "integrity_prevalence")["payload"]["severity"] == expected_severity


@pytest.mark.parametrize(
    "observed, expected_severity",
    [
        (0.75, "warn"),     # < warn(0.80)
        (0.60, "breaker"),  # < breaker(0.65)
        (0.40, "halt"),     # < halt(0.50)
    ],
)
def test_severity_picks_lowest_crossed_for_floor(observed, expected_severity):
    state = _tick_state(creator_supply=observed)
    events = check(state, recent_events=[], params=_params())
    assert _by_kind(events, "creator_supply_collapse")["payload"]["severity"] == expected_severity


# -----------------------------------------------------------------------------
# 4. Substrate-writer structural validation
# -----------------------------------------------------------------------------

def test_emitted_events_pass_substrate_writer_validation():
    """Every emitted event must validate against substrate/writer.py's _prepare.

    This is the structural check the M4a contract requires: the writer is
    the single source of truth for what shapes are accepted, so we run
    the same validation here (without actually inserting).
    """
    state = _tick_state(
        prevalence_state={"violating_prevalence": 0.025},     # halt
        segment_state_map={"teen": {"integrity_exposure": 0.070}},  # halt
        creator_supply=0.40,                                  # halt (below)
        ad_load_observed=0.40,                                # halt
    )
    events = check(state, recent_events=[], params=_params())

    # All four breakers fire.
    assert _kinds_in(events) == set(GUARDRAIL_KINDS)
    # intra_day_seq must be unique within the tick.
    seqs = [e["intra_day_seq"] for e in events]
    assert len(set(seqs)) == len(seqs)

    for ev in events:
        # _prepare raises EventValidationError on any structural problem.
        prepared = _prepare(ev)
        assert prepared["event_type"] == "guardrail_fired"
        assert "guardrail_kind" in prepared["payload"]
        assert "threshold" in prepared["payload"]
        assert "observed" in prepared["payload"]


def test_invalid_event_would_fail_writer_validation():
    """Sanity: confirm _prepare actually rejects malformed shapes (proves the
    structural test above is non-trivial)."""
    bad = {
        "event_type": "guardrail_fired",
        # Missing scenario_id/tick_day/intra_day_seq/timestamp.
        "payload": {"guardrail_kind": "x", "threshold": 1.0, "observed": 2.0},
    }
    with pytest.raises(EventValidationError):
        _prepare(bad)


def test_inputs_not_mutated():
    """The pure-function contract forbids in-place mutation of inputs."""
    state = _tick_state(ad_load_observed=0.40)
    snapshot = {
        "ad_load_observed": state["ad_load_observed"],
        "intra_day_seq": state["intra_day_seq"],
    }
    recent: list = []
    params = _params()
    check(state, recent_events=recent, params=params)
    assert state["ad_load_observed"] == snapshot["ad_load_observed"]
    assert state["intra_day_seq"] == snapshot["intra_day_seq"]
    assert recent == []
