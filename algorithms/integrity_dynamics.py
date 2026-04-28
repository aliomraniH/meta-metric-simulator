"""Integrity dynamics — pure function, agent-free.

Per docs/ALGORITHMS.md §integrity_dynamics, this module evolves the
violating-prevalence state under organic decay plus any integrity-targeted
perturbations the engine has filtered for this tick.

The function is deterministic — no `rng` argument.  Stochastic shocks
enter the system via perturbations from Layer 6 (scenario compiler);
this layer just applies them.

Output is a new PrevalenceState; emits no events.  The guardrails
algorithm (algorithms/guardrails.py) reads the post-evolve prevalence
and decides whether to fire `guardrail_fired`.

House rules:
  * Pure function.  No rng, no disk I/O.
  * Reads only the params + perturbations dict arguments.
  * Imports nothing from baselines/, engine/, or other algorithms.
"""
from __future__ import annotations

from typing import Any, Mapping


_FALLBACK_DECAY_PER_DAY = 0.02
_FALLBACK_DETECTION_GROWTH = 0.005
_FALLBACK_FLOOR = 0.0
_FALLBACK_CEILING = 1.0


def evolve(
    prevalence_state: Mapping[str, Any],
    perturbations: list[Mapping[str, Any]],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the next PrevalenceState.

    Order of operations:
      1. Organic decay applied to system-wide and per-topic prevalence.
      2. Perturbations applied in input order.  A perturbation with op
         `set` overrides; `add` increments; `multiply` scales.  Default
         is `set` (matches the spike contract).
      3. Detection rate drifts toward 1.0 by `detection_growth_per_day`.
      4. Final clamp into [floor, ceiling].

    Args:
        prevalence_state: dict with `violating_prevalence` (float ∈ [0,1]),
            `detection_rate` (float ∈ [0,1]), and optional `by_topic`
            (dict[str, float]).
        perturbations: list of integrity-targeted perturbations, each
            with `target`, `value`, and optional `op` ∈ {set, add,
            multiply}.  The engine has already filtered to those active
            on this tick.
        params: integrity_dynamics.yaml sub-dict.  Recognised keys:
            `organic_decay_per_day`, `detection_growth_per_day`, `floor`,
            `ceiling`.

    Returns:
        A new dict — input is not mutated.
    """
    decay = float(params.get("organic_decay_per_day", _FALLBACK_DECAY_PER_DAY))
    detection_growth = float(params.get("detection_growth_per_day", _FALLBACK_DETECTION_GROWTH))
    floor = float(params.get("floor", _FALLBACK_FLOOR))
    ceiling = float(params.get("ceiling", _FALLBACK_CEILING))
    if ceiling < floor:
        ceiling = floor

    # Start from a copy so the input is not mutated.
    prevalence = float(prevalence_state.get("violating_prevalence", 0.0))
    detection = float(prevalence_state.get("detection_rate", 0.0))
    by_topic = dict(prevalence_state.get("by_topic") or {})

    # 1. Organic decay.
    prevalence = prevalence * (1.0 - decay)
    by_topic = {t: v * (1.0 - decay) for t, v in by_topic.items()}

    # 2. Perturbations.
    for p in perturbations or ():
        target = p.get("target")
        op = p.get("op", "set")
        value = float(p.get("value", 0.0))

        if target == "violating_prevalence":
            prevalence = _apply_op(prevalence, op, value)
        elif target == "detection_rate":
            detection = _apply_op(detection, op, value)
        elif isinstance(target, str) and target.startswith("by_topic."):
            topic = target.split(".", 1)[1]
            by_topic[topic] = _apply_op(by_topic.get(topic, 0.0), op, value)
        # Unrecognised targets are silently ignored — the engine validates
        # perturbation manifests at compile time (M15), so reaching here
        # with a bad target means upstream filtering let one slip; we
        # don't want a runtime error mid-tick.

    # 3. Detection rate growth — drift toward 1.0 each day.
    detection = detection + detection_growth * (1.0 - detection)

    # 4. Clamp everything.
    prevalence = _clamp(prevalence, floor, ceiling)
    detection = _clamp(detection, 0.0, 1.0)
    by_topic = {t: _clamp(v, floor, ceiling) for t, v in by_topic.items()}

    return {
        "violating_prevalence": prevalence,
        "detection_rate": detection,
        "by_topic": by_topic,
    }


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------

def _apply_op(current: float, op: str, value: float) -> float:
    if op == "set":
        return value
    if op == "add":
        return current + value
    if op == "multiply":
        return current * value
    # Unknown op: treat as set so unrecognised perturbations don't no-op
    # silently against caller expectations.
    return value


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
