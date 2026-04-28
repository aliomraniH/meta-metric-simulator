"""Guardrails algorithm — pure function, agent-free.

Per docs/ALGORITHMS.md §guardrails, this module evaluates the four
circuit-breakers against the post-tick state and returns a list of
`guardrail_fired` event dicts ready for substrate/writer.py.

The four breakers (all required, evaluated every tick):
  - integrity_prevalence       — system-wide violating prevalence ceiling
  - teen_exposure              — segment-scoped integrity exposure ceiling
  - creator_supply_collapse    — posting-rate floor (system or per-tier)
  - ad_load_ceiling            — observed ad-load above threshold

Severity levels (lowest → highest):
  - warn      — advisory; logged but no automated reaction
  - breaker   — distribution tightening / ad-load throttling kicks in
  - halt      — hard stop; engine should pause emissions until a human
                resolves

Each guardrail's params block has the shape:
    {
      "warn":    <float>,
      "breaker": <float>,
      "halt":    <float>,
      "direction": "above"|"below",   # "above" for ceilings, "below" for floors
    }

The highest severity whose threshold is crossed wins.

House rules:
  * Pure function.  No `rng` (guardrails are deterministic threshold
    checks).  No disk I/O.  No imports from upper layers.
  * Returns event dicts; never calls writer.write().  The engine (M7)
    is the sole writer.
  * Inputs are read-only — the function does not mutate `tick_state`
    or `recent_events`.
"""
from __future__ import annotations

from typing import Any, Mapping

# Severity ordering (lowest → highest).  The guardrails check picks the
# top severity whose threshold is crossed and emits one event per kind.
_SEVERITY_ORDER: tuple[str, ...] = ("warn", "breaker", "halt")
_SEVERITY_RANK: dict[str, int] = {s: i for i, s in enumerate(_SEVERITY_ORDER)}

# Required guardrail kinds — evaluated every tick.  Adding a new kind
# means appending here AND adding a corresponding entry to
# params/guardrail_thresholds.yaml.
GUARDRAIL_KINDS: tuple[str, ...] = (
    "integrity_prevalence",
    "teen_exposure",
    "creator_supply_collapse",
    "ad_load_ceiling",
)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def check(
    tick_state: Mapping[str, Any],
    recent_events: list[Mapping[str, Any]],
    params: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate all four breakers and return guardrail_fired events.

    Args:
        tick_state: post-tick view assembled by the engine.  Required
            keys: `scenario_id`, `tick_day`, `intra_day_seq`, `timestamp`,
            `prevalence_state`, `segment_state_map`, `ad_load_observed`.
            Optional: `creator_supply` (system-wide posting rate).
        recent_events: events from the current tick.  Read-only; reserved
            for short-window pattern checks.  Currently unused by the
            four breakers but the contract preserves the argument so
            future guardrails can read it.
        params: guardrail_thresholds.yaml sub-dict keyed by guardrail
            kind, each with `warn`/`breaker`/`halt`/`direction`.

    Returns:
        list of guardrail_fired event dicts.  At most one per kind per
        tick.  Empty list when no thresholds are crossed.
    """
    _ = recent_events  # reserved for future short-window guardrails
    out: list[dict[str, Any]] = []

    base_seq = int(tick_state.get("intra_day_seq", 1))
    timestamp = float(tick_state.get("timestamp", 0.0))
    seq_offset = 0

    # -- 1. integrity prevalence ceiling --------------------------------------
    prev = (tick_state.get("prevalence_state") or {})
    observed = float(prev.get("violating_prevalence", 0.0))
    ev = _maybe_event(
        kind="integrity_prevalence",
        observed=observed,
        thresholds=params.get("integrity_prevalence"),
        tick_state=tick_state,
        intra_day_seq=base_seq + seq_offset,
        timestamp=timestamp,
    )
    if ev is not None:
        out.append(ev)
        seq_offset += 1

    # -- 2. teen exposure ceiling ---------------------------------------------
    segments = tick_state.get("segment_state_map") or {}
    teen = segments.get("teen") or {}
    teen_exposure = float(teen.get("integrity_exposure", 0.0))
    ev = _maybe_event(
        kind="teen_exposure",
        observed=teen_exposure,
        thresholds=params.get("teen_exposure"),
        tick_state=tick_state,
        intra_day_seq=base_seq + seq_offset,
        timestamp=timestamp,
        viewer_segment="teen",
    )
    if ev is not None:
        out.append(ev)
        seq_offset += 1

    # -- 3. creator supply collapse (floor) -----------------------------------
    # Either a system-wide field or the avg posting rate across segments;
    # the engine summarises whichever it has into `creator_supply`.
    supply = float(tick_state.get("creator_supply", 1.0))
    ev = _maybe_event(
        kind="creator_supply_collapse",
        observed=supply,
        thresholds=params.get("creator_supply_collapse"),
        tick_state=tick_state,
        intra_day_seq=base_seq + seq_offset,
        timestamp=timestamp,
    )
    if ev is not None:
        out.append(ev)
        seq_offset += 1

    # -- 4. ad-load ceiling ---------------------------------------------------
    ad_load = float(tick_state.get("ad_load_observed", 0.0))
    ev = _maybe_event(
        kind="ad_load_ceiling",
        observed=ad_load,
        thresholds=params.get("ad_load_ceiling"),
        tick_state=tick_state,
        intra_day_seq=base_seq + seq_offset,
        timestamp=timestamp,
    )
    if ev is not None:
        out.append(ev)
        seq_offset += 1

    return out


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------

def _maybe_event(
    *,
    kind: str,
    observed: float,
    thresholds: Mapping[str, Any] | None,
    tick_state: Mapping[str, Any],
    intra_day_seq: int,
    timestamp: float,
    viewer_segment: str | None = None,
) -> dict[str, Any] | None:
    """Return a guardrail_fired event if any threshold is crossed, else None.

    Picks the highest severity whose threshold is crossed.
    """
    if not thresholds:
        return None
    direction = thresholds.get("direction", "above")
    severity = _select_severity(observed, thresholds, direction)
    if severity is None:
        return None

    crossed_threshold = float(thresholds.get(severity, 0.0))

    payload: dict[str, Any] = {
        "guardrail_kind": kind,
        "threshold": crossed_threshold,
        "observed": observed,
        "severity": severity,
        "direction": direction,
    }

    event: dict[str, Any] = {
        "scenario_id": tick_state.get("scenario_id"),
        "tick_day": int(tick_state.get("tick_day", 0)),
        "intra_day_seq": int(intra_day_seq),
        "timestamp": float(timestamp),
        "event_type": "guardrail_fired",
        "payload": payload,
    }
    if viewer_segment is not None:
        event["viewer_segment"] = viewer_segment
    return event


def _select_severity(
    observed: float,
    thresholds: Mapping[str, Any],
    direction: str,
) -> str | None:
    """Pick the highest-severity threshold the observation crosses.

    For `direction == "above"` (ceilings): observed > threshold.
    For `direction == "below"` (floors):  observed < threshold.
    """
    crossed: str | None = None
    for sev in _SEVERITY_ORDER:
        if sev not in thresholds:
            continue
        try:
            t = float(thresholds[sev])
        except (TypeError, ValueError):
            continue
        if direction == "above" and observed > t:
            crossed = sev
        elif direction == "below" and observed < t:
            crossed = sev
        # _SEVERITY_ORDER is low → high so the last-wins assignment is
        # the highest crossed severity.
    return crossed
