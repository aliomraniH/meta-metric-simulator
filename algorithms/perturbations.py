"""Perturbations — pure function, agent-free.

Per docs/ALGORITHMS.md §perturbations, this module applies a single
perturbation_def to the running tick's state bag at the given tick_day.

Three supported shapes:
  * `{type: "ramp",  target, from, to, days}`
      Linear interpolation over `days` days.  At tick_day=0 → `from`;
      at tick_day=days → `to`; clamped at the endpoints outside
      that range.
  * `{type: "step",  target, value}`
      Instant: state[target] = value.
  * `{type: "spike", target, to, duration_days, baseline}`
      For tick_day in [0, duration_days] → `to`.
      For tick_day > duration_days → `baseline`.
      The engine fills `baseline` from the pre-spike state when the
      perturbation is registered; if absent, the function leaves the
      target unchanged once duration elapses.

The `target` is a dotted path (e.g. `monetization.ad_load`) inside the
state bag.  Unknown paths raise KeyError — the engine validates the
manifest at scenario-compile time (M15) so reaching here with a bad
target means upstream filtering let one slip.

House rules:
  * Pure function.  No rng — perturbations are deterministic by
    construction.  Stochastic shocks belong elsewhere.
  * Inputs are not mutated; the returned state is a fresh dict.
  * Imports nothing from baselines/, engine/, or other algorithms.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping


def apply(
    state: Mapping[str, Any],
    perturbation_def: Mapping[str, Any],
    tick_day: int,
) -> dict[str, Any]:
    """Apply one perturbation to the state bag and return a new state.

    Args:
        state: the running tick state bag.  Treated read-only.
        perturbation_def: the perturbation spec; see module docstring.
        tick_day: the tick index relative to the perturbation's start
            (the engine offsets this when a perturbation has its own
            start_day).

    Returns:
        A deep-copied state dict with the target path updated.  Raises
        `KeyError` if the target's parent path doesn't exist in state,
        or `ValueError` for malformed perturbation_defs.
    """
    ptype = perturbation_def.get("type")
    target = perturbation_def.get("target")
    if not isinstance(target, str) or not target:
        raise ValueError(f"perturbation missing 'target': {perturbation_def!r}")

    if ptype == "ramp":
        new_value = _ramp_value(perturbation_def, tick_day)
    elif ptype == "step":
        if "value" not in perturbation_def:
            raise ValueError(f"step perturbation missing 'value': {perturbation_def!r}")
        new_value = perturbation_def["value"]
    elif ptype == "spike":
        new_value = _spike_value(perturbation_def, tick_day, state, target)
        if new_value is _UNCHANGED:
            return copy.deepcopy(dict(state))
    else:
        raise ValueError(f"unknown perturbation type {ptype!r}")

    next_state = copy.deepcopy(dict(state))
    _set_path(next_state, target, new_value)
    return next_state


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------

# Sentinel returned by the spike helper when post-duration baseline is
# unspecified and the function should leave the target unchanged.
class _Unchanged:
    __slots__ = ()
_UNCHANGED = _Unchanged()


def _ramp_value(p: Mapping[str, Any], tick_day: int) -> float:
    if "from" not in p or "to" not in p or "days" not in p:
        raise ValueError(f"ramp perturbation missing from/to/days: {p!r}")
    a = float(p["from"])
    b = float(p["to"])
    days = int(p["days"])
    if days <= 0:
        # Zero-day ramp degenerates to a step at `to`.
        return b
    if tick_day <= 0:
        return a
    if tick_day >= days:
        return b
    return a + (b - a) * (tick_day / days)


def _spike_value(
    p: Mapping[str, Any],
    tick_day: int,
    state: Mapping[str, Any],
    target: str,
) -> Any:
    if "to" not in p or "duration_days" not in p:
        raise ValueError(f"spike perturbation missing to/duration_days: {p!r}")
    duration = int(p["duration_days"])
    if tick_day < 0:
        return _UNCHANGED
    # Active for tick_day in [0, duration_days].  Returns to baseline
    # at tick_day == duration_days + 1 — the boundary the M4c test pins.
    if tick_day <= duration:
        return p["to"]

    # Post-spike: the engine should have stamped a `baseline` onto the
    # def when the spike was registered.  Fall back to reading from the
    # current state if the engine didn't (e.g. unit-test direct calls).
    if "baseline" in p:
        return p["baseline"]
    try:
        return _get_path(state, target)
    except KeyError:
        return _UNCHANGED


def _split_path(path: str) -> list[str]:
    return path.split(".")


def _get_path(state: Mapping[str, Any], path: str) -> Any:
    node: Any = state
    for part in _split_path(path):
        if not isinstance(node, Mapping) or part not in node:
            raise KeyError(path)
        node = node[part]
    return node


def _set_path(state: dict[str, Any], path: str, value: Any) -> None:
    parts = _split_path(path)
    node: Any = state
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise KeyError(path)
        node = node[part]
    if not isinstance(node, dict):
        raise KeyError(path)
    node[parts[-1]] = value
