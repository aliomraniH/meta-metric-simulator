"""Unit tests for algorithms/creator_response.py.

Per the M4c contract:
  1. Earnings-to-posting elasticity matches the expected sign — earnings
     up → posting up; earnings to 0 → posting drops.
  2. Posting lag is honoured — response is delayed by `lag_days` ticks.
  3. Tier multipliers are applied correctly.
  4. Determinism — same input → same output bit-for-bit.
"""
from __future__ import annotations

import copy

from algorithms.creator_response import update_supply


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

def _params(
    lag: int = 3,
    elasticity: float = 0.5,
    smoothing: float = 1.0,  # smoothing=1 → fully responsive each tick (cleaner asserts)
) -> dict:
    return {
        "posting_lag_days": lag,
        "earnings_to_posting_elasticity": elasticity,
        "smoothing": smoothing,
        "posting_floor_per_day": 0.05,
        "posting_ceiling_per_day": 5.0,
        "tier_multipliers": {
            "nano": 1.5,
            "micro": 1.2,
            "mid": 1.0,
            "macro": 0.8,
            "mega": 0.5,
        },
    }


def _creator(tier: str = "mid", baseline_rate: float = 1.0, baseline_earnings: float = 10.0) -> dict:
    return {
        "creator_id": "c-1",
        "tier": tier,
        "posting_rate_per_day": baseline_rate,
        "baseline_posting_rate": baseline_rate,
        "baseline_earnings": baseline_earnings,
        "earnings_history": [],
    }


def _run_n_ticks(creator: dict, earnings_per_tick: list[float], params: dict) -> list[dict]:
    """Apply update_supply across the given earnings sequence, return states per tick."""
    states = []
    state = creator
    for e in earnings_per_tick:
        state = update_supply(state, e, params)
        states.append(state)
    return states


# -----------------------------------------------------------------------------
# 1. Elasticity sign
# -----------------------------------------------------------------------------

def test_earnings_above_baseline_raises_posting_rate():
    params = _params(lag=0)  # no lag so the response shows immediately
    creator = _creator()
    out = update_supply(creator, earnings_this_tick=20.0, params=params)
    assert out["posting_rate_per_day"] > creator["posting_rate_per_day"]


def test_earnings_below_baseline_lowers_posting_rate():
    params = _params(lag=0)
    creator = _creator()
    out = update_supply(creator, earnings_this_tick=2.0, params=params)
    assert out["posting_rate_per_day"] < creator["posting_rate_per_day"]


def test_earnings_at_baseline_keeps_rate_at_baseline():
    params = _params(lag=0)
    creator = _creator()
    out = update_supply(creator, earnings_this_tick=10.0, params=params)
    # earnings_ratio == 1.0 → response_factor == 1.0 → unchanged.
    assert abs(out["posting_rate_per_day"] - creator["posting_rate_per_day"]) < 1e-9


def test_earnings_to_zero_drops_rate_substantially():
    params = _params(lag=0, elasticity=1.0)
    creator = _creator()
    out = update_supply(creator, earnings_this_tick=0.0, params=params)
    # ratio=0 → response = 1 - 1*tier_mult*1 = 0 (mid tier_mult=1.0).
    # smoothing=1.0 → target_rate = baseline * 0 = 0 → clamped to floor.
    assert out["posting_rate_per_day"] == params["posting_floor_per_day"]


# -----------------------------------------------------------------------------
# 2. Lag honoured
# -----------------------------------------------------------------------------

def test_posting_rate_unchanged_until_lag_window_passes():
    """With lag=3, the rate should not move for the first 3 ticks even if
    earnings spike at tick 0."""
    params = _params(lag=3)
    creator = _creator()
    states = _run_n_ticks(creator, [50.0, 50.0, 50.0, 50.0], params)
    # Tick 0..2: history shorter than (lag_days+1)=4 → no update yet.
    assert states[0]["posting_rate_per_day"] == creator["posting_rate_per_day"]
    assert states[1]["posting_rate_per_day"] == creator["posting_rate_per_day"]
    assert states[2]["posting_rate_per_day"] == creator["posting_rate_per_day"]
    # Tick 3: history length is now 4, which equals lag_days+1, so the
    # head (tick-0 earnings of 50) drives the update.
    assert states[3]["posting_rate_per_day"] > creator["posting_rate_per_day"]


def test_lag_zero_means_immediate_response():
    params = _params(lag=0)
    creator = _creator()
    out = update_supply(creator, 50.0, params)
    assert out["posting_rate_per_day"] > creator["posting_rate_per_day"]


def test_history_truncated_to_lag_plus_one():
    params = _params(lag=2)
    creator = _creator()
    states = _run_n_ticks(creator, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], params)
    final_history = states[-1]["earnings_history"]
    assert len(final_history) == 3  # lag_days + 1
    # Most recent three earnings are 4, 5, 6 — the oldest popped off.
    assert final_history == [4.0, 5.0, 6.0]


# -----------------------------------------------------------------------------
# 3. Tier multipliers
# -----------------------------------------------------------------------------

def test_nano_responds_more_than_mega_for_same_earnings_change():
    """Higher tier_multiplier amplifies the elasticity response."""
    params = _params(lag=0)
    earnings = 30.0  # 3x baseline
    nano = _creator(tier="nano")
    mega = _creator(tier="mega")
    nano_out = update_supply(nano, earnings, params)
    mega_out = update_supply(mega, earnings, params)
    nano_delta = nano_out["posting_rate_per_day"] - nano["posting_rate_per_day"]
    mega_delta = mega_out["posting_rate_per_day"] - mega["posting_rate_per_day"]
    assert nano_delta > mega_delta


def test_unknown_tier_uses_default_multiplier():
    params = _params(lag=0)
    creator = _creator(tier="extragalactic")  # not in tier_multipliers
    out = update_supply(creator, 20.0, params)
    # Default mult is 1.0 → equivalent to the mid tier.
    mid_out = update_supply(_creator(tier="mid"), 20.0, params)
    assert abs(out["posting_rate_per_day"] - mid_out["posting_rate_per_day"]) < 1e-9


# -----------------------------------------------------------------------------
# 4. Determinism + invariants
# -----------------------------------------------------------------------------

def test_update_supply_is_bit_for_bit_deterministic():
    params = _params(lag=2)
    creator = _creator()
    a = update_supply(creator, 15.0, params)
    b = update_supply(creator, 15.0, params)
    assert a == b


def test_input_state_not_mutated():
    params = _params(lag=2)
    creator = _creator()
    snapshot = copy.deepcopy(creator)
    update_supply(creator, 15.0, params)
    assert creator == snapshot


def test_posting_rate_clamped_to_ceiling_under_huge_earnings():
    params = _params(lag=0, elasticity=10.0)
    creator = _creator()
    out = update_supply(creator, earnings_this_tick=10_000.0, params=params)
    assert out["posting_rate_per_day"] == params["posting_ceiling_per_day"]


def test_baseline_earnings_zero_leaves_rate_unchanged():
    """A new creator with no baseline should not have their rate yanked."""
    params = _params(lag=0)
    creator = _creator(baseline_earnings=0.0)
    out = update_supply(creator, 5.0, params)
    assert out["posting_rate_per_day"] == creator["posting_rate_per_day"]
