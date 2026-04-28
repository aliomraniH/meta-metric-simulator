"""Unit tests for algorithms/segment_cascades.py.

Per the M4c contract:
  1. Population conservation: sum of population_share_pct invariant
     under propagation, within float tolerance.
  2. Teen guardrail change propagates to other segments per the matrix.
  3. Determinism — no rng; same input → same output bit-for-bit.
"""
from __future__ import annotations

import copy

from algorithms.segment_cascades import propagate


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

def _segments(teen_share: float = 20.0) -> dict:
    """Four-segment baseline summing to 100% population share."""
    return {
        "teen": {
            "population_share_pct": teen_share,
            "watch_time_per_session": 18.0,
            "integrity_exposure": 0.012,
        },
        "young_adult": {
            "population_share_pct": 30.0,
            "watch_time_per_session": 22.0,
            "integrity_exposure": 0.008,
        },
        "snacker": {
            "population_share_pct": 30.0,
            "watch_time_per_session": 12.0,
            "integrity_exposure": 0.006,
        },
        "lean_back": {
            "population_share_pct": 100.0 - 20.0 - 30.0 - 30.0,  # whatever balances
            "watch_time_per_session": 30.0,
            "integrity_exposure": 0.004,
        },
    }


def _matrix() -> dict:
    """Substitution matrix where each row sums to 1.

    Teens leak 30% of their share to young_adult and snacker; the other
    three segments are sticky (mostly stay).
    """
    return {
        "teen":        {"teen": 0.70, "young_adult": 0.20, "snacker": 0.10},
        "young_adult": {"young_adult": 0.90, "snacker": 0.05, "lean_back": 0.05},
        "snacker":     {"snacker": 0.95, "young_adult": 0.05},
        "lean_back":   {"lean_back": 1.00},
    }


def _params() -> dict:
    return {"substitution_matrix": _matrix()}


def _total(segments: dict) -> float:
    return sum(s.get("population_share_pct", 0.0) for s in segments.values())


# -----------------------------------------------------------------------------
# 1. Population conservation
# -----------------------------------------------------------------------------

def test_population_share_sum_invariant():
    segs = _segments()
    before_total = _total(segs)
    out = propagate(segs, _params())
    after_total = _total(out)
    assert abs(after_total - before_total) < 1e-9


def test_population_share_sum_invariant_after_many_iterations():
    """Apply propagate repeatedly; total shouldn't drift."""
    segs = _segments()
    before_total = _total(segs)
    for _ in range(50):
        segs = propagate(segs, _params())
    assert abs(_total(segs) - before_total) < 1e-6


def test_leaky_matrix_rescaled_to_conserve_mass():
    """If a row doesn't sum to 1.0, the output is rescaled to the original total."""
    segs = _segments()
    leaky = {
        "teen":        {"teen": 0.50, "young_adult": 0.10},  # row sums to 0.60
        "young_adult": {"young_adult": 1.00},
        "snacker":     {"snacker": 1.00},
        "lean_back":   {"lean_back": 1.00},
    }
    before_total = _total(segs)
    out = propagate(segs, {"substitution_matrix": leaky})
    assert abs(_total(out) - before_total) < 1e-6


# -----------------------------------------------------------------------------
# 2. Teen guardrail change propagates
# -----------------------------------------------------------------------------

def test_teen_drop_changes_destination_segments():
    """Lowering teen's input share (simulating a guardrail tightening
    teen distribution) changes the absolute share at the destinations
    teen's matrix row routes to.  Mass is conserved at whatever total the
    input had — `total_in == total_out` — so a reduced teen input means
    reduced inflow into young_adult and snacker too, and the deltas are
    consistent with the matrix coefficients."""
    full = propagate(_segments(teen_share=20.0), _params())
    shocked = propagate(_segments(teen_share=10.0), _params())
    # Teen output share drops because teen input dropped.
    assert shocked["teen"]["population_share_pct"] < full["teen"]["population_share_pct"]
    # young_adult and snacker receive less inflow from teen, so their
    # outputs are also lower.  The cascade has propagated the teen
    # change into these neighbour segments.
    assert shocked["young_adult"]["population_share_pct"] < full["young_adult"]["population_share_pct"]
    assert shocked["snacker"]["population_share_pct"] < full["snacker"]["population_share_pct"]
    # lean_back doesn't appear in teen's row, so its output is unchanged
    # by teen's input shock (it's a sticky identity segment in this fixture).
    assert shocked["lean_back"]["population_share_pct"] == full["lean_back"]["population_share_pct"]


def test_teen_outflow_routed_per_matrix():
    """With the matrix where teen → 30% leaks (20% to young_adult,
    10% to snacker), exactly that share of the teen mass should appear
    as a delta in those segments after one tick."""
    segs = _segments(teen_share=20.0)
    matrix = {
        "teen":        {"teen": 0.70, "young_adult": 0.20, "snacker": 0.10},
        "young_adult": {"young_adult": 1.00},
        "snacker":     {"snacker": 1.00},
        "lean_back":   {"lean_back": 1.00},
    }
    out = propagate(segs, {"substitution_matrix": matrix})
    # Teen 20 → 14 stays = 6 leaves: 4 → young_adult, 2 → snacker.
    assert abs(out["teen"]["population_share_pct"] - 14.0) < 1e-9
    assert abs(out["young_adult"]["population_share_pct"] - (30.0 + 4.0)) < 1e-9
    assert abs(out["snacker"]["population_share_pct"] - (30.0 + 2.0)) < 1e-9
    assert abs(out["lean_back"]["population_share_pct"] - 20.0) < 1e-9


def test_non_conserved_fields_pass_through_unchanged():
    """Watch time, integrity exposure, etc are not redistributed —
    they belong to the segment as a property, not as mass."""
    segs = _segments()
    out = propagate(segs, _params())
    for seg, before in segs.items():
        if seg in out:
            assert out[seg]["watch_time_per_session"] == before["watch_time_per_session"]
            assert out[seg]["integrity_exposure"] == before["integrity_exposure"]


# -----------------------------------------------------------------------------
# 3. Determinism + immutability
# -----------------------------------------------------------------------------

def test_propagate_is_bit_for_bit_deterministic():
    segs = _segments()
    a = propagate(segs, _params())
    b = propagate(segs, _params())
    assert a == b


def test_propagate_does_not_mutate_inputs():
    segs = _segments()
    snap = copy.deepcopy(segs)
    propagate(segs, _params())
    assert segs == snap


def test_segment_with_no_matrix_row_is_identity():
    """A segment missing from the matrix should keep its full share."""
    segs = _segments()
    matrix = {
        "teen": {"teen": 0.50, "young_adult": 0.50},
        # young_adult, snacker, lean_back missing → identity.
    }
    out = propagate(segs, {"substitution_matrix": matrix})
    assert out["snacker"]["population_share_pct"] == segs["snacker"]["population_share_pct"]
    assert out["lean_back"]["population_share_pct"] == segs["lean_back"]["population_share_pct"]
