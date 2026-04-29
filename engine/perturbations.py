"""Engine wrapper around algorithms/perturbations.apply.

The algorithm in algorithms/perturbations.py (M4c) handles a SINGLE
perturbation_def applied at a given tick_day.  The engine has a LIST of
perturbations and calls them every tick.  This wrapper does that, with
two additional concerns the algorithm doesn't carry:

  1. start_day offsetting.  algorithms/perturbations.apply treats its
     `tick_day` argument as relative to the perturbation's start.  The
     engine knows the absolute tick_day and the perturbation's optional
     `start_day` field (default 0); we subtract before calling apply.
     Perturbations whose start_day is in the future are skipped.

  2. spike baseline preservation.  algorithms/perturbations._spike_value
     reads the `baseline` field from the perturbation_def when restoring
     after duration.  The engine stamps `baseline` onto each spike def
     the first time it is applied (capturing the pre-spike state value)
     so subsequent ticks restore to the right number.

This wrapper does NOT re-implement ramp/step/spike math.  Any change to
how perturbation values are computed must happen in algorithms/.
"""
from __future__ import annotations

from typing import Any

from algorithms.perturbations import _get_path  # type: ignore[attr-defined]
from algorithms.perturbations import apply as _algo_apply


def apply_active_perturbations(
    state: dict[str, Any],
    perturbation_defs: list[dict[str, Any]],
    tick_day: int,
) -> dict[str, Any]:
    """Apply every active perturbation in order; return the new state.

    A perturbation is "active" if `tick_day >= start_day`.  Once active,
    the algorithm's apply handles whether the value is on its ramp / step
    / spike phase or back to baseline.

    Args:
        state: the running tick state bag.
        perturbation_defs: the scenario's full perturbation list.  Order
            matters — later perturbations see the state after earlier
            ones have applied.
        tick_day: absolute tick_day (0-based) within the scenario.

    Returns:
        A new state dict; input is not mutated.
    """
    current = dict(state)  # shallow copy is fine; algo deep-copies internally
    for pdef in perturbation_defs or ():
        start_day = int(pdef.get("start_day", 0))
        if tick_day < start_day:
            continue

        # On a spike's first applied tick, capture the baseline value
        # from current state so post-duration restoration has something
        # to restore TO.  Stamp it onto the def in place — subsequent
        # ticks see the captured baseline.
        if pdef.get("type") == "spike" and "baseline" not in pdef:
            try:
                pdef["baseline"] = _get_path(current, pdef["target"])
            except KeyError:
                # Unknown target; the algo's apply will surface the same
                # error with a clearer message.  Let it through.
                pass

        relative_tick = tick_day - start_day
        current = _algo_apply(current, pdef, relative_tick)
    return current
