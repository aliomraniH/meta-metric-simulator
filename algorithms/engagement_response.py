"""Engagement response — pure function, agent-free.

Per docs/ALGORITHMS.md §engagement_response, this module decides what a
viewer does when shown a single impression: skip, watch, or complete,
followed by zero or more post-watch actions (like, send, save, comment,
replay).

Outputs match the substrate writer's required fields per
docs/EVENT_SCHEMA.md.

House rules:
  * Pure function.  All randomness via the injected rng.
  * Reads only from the params dict argument; never opens files.
  * Imports nothing from baselines/, engine/, metrics/, or other algorithms.
  * Returns event dicts; the engine writes them.
"""
from __future__ import annotations

import random
from typing import Any, Mapping


# Actions that may chain off a successful (non-skip) watch.  Each has its
# own Bernoulli propensity in the segment params block.
_POST_WATCH_ACTIONS: tuple[str, ...] = (
    "like", "send", "save", "comment", "replay",
)

# Defaults used when a segment is unknown to the params dict.  Kept very
# conservative — a missing segment should produce sensible-but-low rates,
# not silently zero out engagement.
_FALLBACK_PROPENSITIES: dict[str, float] = {
    "skip_threshold_sec": 3.0,
    "p_skip": 0.30,
    "completion_pct_min": 0.55,
    "completion_pct_max": 1.00,
    "view_end_threshold_pct": 0.95,
    "like_propensity":    0.05,
    "send_propensity":    0.01,
    "save_propensity":    0.01,
    "comment_propensity": 0.005,
    "replay_propensity":  0.02,
}

# Cap watch_duration at this when the impression doesn't carry a
# reel_duration_sec — keeps emitted events finite without inventing a
# specific reel length.
_DEFAULT_REEL_DURATION = 15.0


def respond(
    viewer_state: Mapping[str, Any],
    impression: Mapping[str, Any],
    params: Mapping[str, Any],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Sample engagement events for a single impression.

    Args:
        viewer_state: ViewerState dict.  Must carry `segment`; other fields
            (geo, tenure, fatigue) flow through to emitted events when
            present.
        impression: the impression event the viewer is responding to.
            Must carry the substrate fields (`scenario_id`, `tick_day`,
            `intra_day_seq`, `viewer_id`, `reel_id`).  May carry
            `reel_duration_sec`, `creator_id`, `viewer_segment`, etc.
        params: must contain a `segment_propensities` mapping keyed by
            segment name.  Each segment block exposes `skip_threshold_sec`,
            `p_skip`, completion-pct bounds, `view_end_threshold_pct`,
            and a `*_propensity` for each post-watch action.
        rng: injected random.Random — the only source of randomness.

    Returns:
        List of event dicts.  Always at least one of `skip`, `watch`, or
        `view_end`; optionally chained `like`/`send`/`save`/`comment`/`replay`
        events when the viewer didn't skip.
    """
    segment = viewer_state.get("segment", "")
    propensities = _resolve_propensities(segment, params)

    # The engine assigns intra_day_seq numbers; we increment from the
    # impression's seq to keep ordering stable within the response.
    base_seq = int(impression.get("intra_day_seq", 0))
    seq_offset = 1

    reel_duration = float(
        impression.get("reel_duration_sec") or _DEFAULT_REEL_DURATION
    )
    skip_threshold = float(propensities["skip_threshold_sec"])
    p_skip = float(propensities["p_skip"])

    out: list[dict[str, Any]] = []

    # --- skip / watch / view_end ---------------------------------------------
    if rng.random() < p_skip:
        # Skip — watch_duration is below the segment's skip threshold by
        # construction.  We sample (0.5s, skip_threshold) so the substrate
        # invariant (skip → watch_duration ≤ skip_threshold) holds.
        wd = rng.uniform(0.5, max(0.6, skip_threshold))
        out.append(_event(
            "skip",
            impression=impression,
            viewer_state=viewer_state,
            seq=base_seq + seq_offset,
            extra={"watch_duration_sec": wd},
        ))
        return out

    # Not skipped — sample completion percentage and decide watch vs view_end.
    pct_min = float(propensities.get("completion_pct_min", 0.55))
    pct_max = float(propensities.get("completion_pct_max", 1.00))
    if pct_max < pct_min:
        pct_max = pct_min
    completion_pct = rng.uniform(pct_min, pct_max)
    wd = max(skip_threshold, reel_duration * completion_pct)

    view_end_threshold_pct = float(propensities.get("view_end_threshold_pct", 0.95))
    if completion_pct >= view_end_threshold_pct:
        out.append(_event(
            "view_end",
            impression=impression,
            viewer_state=viewer_state,
            seq=base_seq + seq_offset,
            extra={
                "watch_duration_sec": wd,
                "payload": {"completion_pct": round(completion_pct, 4)},
            },
        ))
    else:
        out.append(_event(
            "watch",
            impression=impression,
            viewer_state=viewer_state,
            seq=base_seq + seq_offset,
            extra={"watch_duration_sec": wd},
        ))
    seq_offset += 1

    # --- post-watch actions (independent Bernoulli trials) ------------------
    for action in _POST_WATCH_ACTIONS:
        prob = float(propensities.get(f"{action}_propensity", 0.0))
        if prob <= 0.0:
            continue
        if rng.random() < prob:
            extra: dict[str, Any] = {}
            if action == "replay":
                # A replay is also a watch, so carry watch_duration through.
                extra["watch_duration_sec"] = wd
            out.append(_event(
                action,
                impression=impression,
                viewer_state=viewer_state,
                seq=base_seq + seq_offset,
                extra=extra,
            ))
            seq_offset += 1

    return out


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------

def _resolve_propensities(segment: str, params: Mapping[str, Any]) -> dict[str, Any]:
    by_segment = params.get("segment_propensities") or {}
    block = by_segment.get(segment)
    if not block:
        return dict(_FALLBACK_PROPENSITIES)
    merged = dict(_FALLBACK_PROPENSITIES)
    merged.update(block)
    return merged


def _event(
    event_type: str,
    *,
    impression: Mapping[str, Any],
    viewer_state: Mapping[str, Any],
    seq: int,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    """Compose a substrate-shaped event from impression context + extras.

    Carries through scenario/tick/timestamp from the impression.  `extra`
    is layered on last so callers can override per-event fields like
    `watch_duration_sec` or `payload`.
    """
    ev: dict[str, Any] = {
        "scenario_id": impression.get("scenario_id"),
        "tick_day": impression.get("tick_day"),
        "intra_day_seq": int(seq),
        "timestamp": impression.get("timestamp"),
        "event_type": event_type,
        "viewer_id": impression.get("viewer_id"),
        "viewer_segment": (
            impression.get("viewer_segment")
            or viewer_state.get("segment")
        ),
        "viewer_geo": impression.get("viewer_geo") or viewer_state.get("geo"),
        "viewer_tenure_days": (
            impression.get("viewer_tenure_days")
            or viewer_state.get("tenure_days")
        ),
        "creator_id": impression.get("creator_id"),
        "creator_tier": impression.get("creator_tier"),
        "reel_id": impression.get("reel_id"),
        "reel_duration_sec": impression.get("reel_duration_sec"),
        "reel_topic_cluster": impression.get("reel_topic_cluster"),
    }
    ev.update(extra)
    return ev
