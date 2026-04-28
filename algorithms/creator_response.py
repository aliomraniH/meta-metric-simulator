"""Creator response — pure function, agent-free.

Per docs/ALGORITHMS.md §creator_response, this module updates a creator's
posting rate based on their earnings, with a per-tier elasticity and a
configurable lag (so today's earnings drive posting `lag_days` ticks
from now, mirroring the real platform's delay between revenue signal
and supply response).

Output is a new CreatorState; the engine (M7) emits the actual
`creator_post` events using the updated `posting_rate_per_day`.

House rules:
  * Pure function.  No rng (the M3 contract allowed an optional noise
    term but the M4c spec is deterministic).  No disk I/O.
  * Reads only the params dict.
  * Imports nothing from baselines/, engine/, or other algorithms.
  * Inputs are not mutated — a fresh dict is returned.
"""
from __future__ import annotations

from typing import Any, Mapping


_FALLBACK_LAG_DAYS = 3
_FALLBACK_ELASTICITY = 0.5
_FALLBACK_SMOOTHING = 0.5
_FALLBACK_POSTING_FLOOR = 0.05
_FALLBACK_POSTING_CEILING = 5.0
_FALLBACK_TIER_MULT = 1.0


def update_supply(
    creator_state: Mapping[str, Any],
    earnings_this_tick: float,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the next CreatorState after one tick of earnings feedback.

    Args:
        creator_state: dict carrying at minimum
            `posting_rate_per_day`, `baseline_posting_rate`,
            `baseline_earnings`, `tier`, and `earnings_history` (a list
            of floats acting as a FIFO of recent earnings, length up to
            `lag_days + 1`).  Missing fields fall through to sensible
            defaults so a freshly-minted creator can be passed in.
        earnings_this_tick: the creator's ad revenue for the tick that
            just finished, summarised by the engine.
        params: creator_economics.yaml sub-dict.  Recognised keys:
            `posting_lag_days`, `earnings_to_posting_elasticity`,
            `tier_multipliers`, `posting_floor_per_day`,
            `posting_ceiling_per_day`, `smoothing`.

    Returns:
        New CreatorState — the input dict is not mutated.
    """
    lag_days = int(params.get("posting_lag_days", _FALLBACK_LAG_DAYS))
    elasticity = float(params.get("earnings_to_posting_elasticity", _FALLBACK_ELASTICITY))
    smoothing = float(params.get("smoothing", _FALLBACK_SMOOTHING))
    floor = float(params.get("posting_floor_per_day", _FALLBACK_POSTING_FLOOR))
    ceiling = float(params.get("posting_ceiling_per_day", _FALLBACK_POSTING_CEILING))
    if ceiling < floor:
        ceiling = floor
    tier_multipliers = params.get("tier_multipliers") or {}

    tier = creator_state.get("tier", "")
    tier_mult = float(tier_multipliers.get(tier, _FALLBACK_TIER_MULT))

    current_rate = float(creator_state.get("posting_rate_per_day", floor))
    baseline_rate = float(creator_state.get("baseline_posting_rate", current_rate))
    baseline_earnings = float(creator_state.get("baseline_earnings", 0.0))

    # FIFO of length lag_days + 1 — the head is the value `lag_days` ticks
    # behind, which is what drives this tick's response.
    history = list(creator_state.get("earnings_history") or [])
    history.append(float(earnings_this_tick))
    while len(history) > lag_days + 1:
        history.pop(0)

    next_state: dict[str, Any] = {
        **creator_state,
        "earnings_history": history,
    }

    # Not enough history yet to apply the lagged signal — leave rate alone
    # but persist the appended history so the next tick can act on it.
    if len(history) <= lag_days:
        return next_state

    effective_earnings = float(history[0])

    # When baseline_earnings is zero we can't form a ratio; fall back to
    # leaving the posting rate unchanged so a brand-new creator with no
    # baseline doesn't have their rate yanked to zero on the first tick.
    if baseline_earnings <= 0.0:
        return next_state

    earnings_ratio = effective_earnings / baseline_earnings
    response_factor = 1.0 + elasticity * tier_mult * (earnings_ratio - 1.0)
    target_rate = baseline_rate * response_factor

    smoothed = (1.0 - smoothing) * current_rate + smoothing * target_rate
    next_state["posting_rate_per_day"] = _clamp(smoothed, floor, ceiling)
    return next_state


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
