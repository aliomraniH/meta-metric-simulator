"""Monetization — pure function, agent-free.

Per docs/ALGORITHMS.md §monetization, this module decides whether an
impression slot is monetised.  When yes, it samples an eCPM, computes
ad_revenue_usd for the single impression, and returns an `ad_impression`
event ready for the substrate writer.  When no, it returns None.

The viewer's reaction to the ad (skip elasticity) is handled by the
engagement_response algorithm on the next impression — this function does
NOT call out to it.

House rules:
  * Pure function.  All randomness via the injected rng.
  * Reads only the params + ad_load_policy dict arguments.
  * Imports nothing from baselines/, engine/, or other algorithms.
"""
from __future__ import annotations

import random
from typing import Any, Mapping


# Defaults for params.monetization_curves.  Conservative numbers chosen so
# tests that omit a field still produce a non-degenerate distribution.
_FALLBACK_ECPM_MEAN = 8.0
_FALLBACK_ECPM_SD = 2.0
_FALLBACK_ECPM_FLOOR = 0.5
_FALLBACK_ECPM_CEILING = 30.0
_FALLBACK_REELS_EFFICIENCY = 0.90
_FALLBACK_CONVERSION_LIFT = 1.0

# Surfaces that the §4.2 Reels-vs-Feed efficiency multiplier applies to.
# Match both the canonical "reels_tab" the engine emits and a bare "reels"
# in case scenario authors set the surface field directly.
_REELS_SURFACES: frozenset[str] = frozenset({"reels", "reels_tab"})


def maybe_ad(
    impression: Mapping[str, Any],
    ad_load_policy: Mapping[str, Any],
    params: Mapping[str, Any],
    rng: random.Random,
) -> dict[str, Any] | None:
    """Decide whether this slot is an ad and, if so, build the event.

    Args:
        impression: the organic impression event the slot was about to
            serve.  Carries scenario/tick/timestamp/viewer fields the ad
            event inherits.
        ad_load_policy: per-tick ad-load policy with `probability` ∈ [0, 1].
            The engine merges any active perturbation into this dict
            before passing.
        params: monetization curves.  Recognised keys (with sensible
            fallbacks): `ecpm_mean`, `ecpm_sd`, `ecpm_floor`, `ecpm_ceiling`,
            an optional `by_segment` map keyed by viewer_segment for
            per-segment overrides, `reels_vs_feed_efficiency_ratio`
            (multiplied into eCPM when the impression's surface is in
            _REELS_SURFACES, per reels_metrics_comprehensive_v2.md §4.2),
            and `conversion_lift_propagation` (multiplied into the final
            ad_revenue_usd, used by calibration test_04 to model GEM-style
            targeting lifts per §11.4).
        rng: injected random.Random.

    Returns:
        An `ad_impression` event dict, or None when the slot is organic.
    """
    probability = float(ad_load_policy.get("probability", 0.0))
    probability = max(0.0, min(1.0, probability))
    if rng.random() >= probability:
        return None

    segment = impression.get("viewer_segment")
    curve = _resolve_curve(segment, params)

    # Reels-vs-Feed eCPM efficiency (§4.2): Reels ad slots clear at
    # ~0.85-0.95× Feed eCPM.  Apply only when the impression originated
    # from a Reels surface; ads in other surfaces (Feed, Stories) are
    # unmodified.  Applied INSIDE _sample_ecpm so the floor/ceiling
    # clamp wraps the post-efficiency value — bounds are on what
    # actually clears, not the pre-efficiency Feed-equivalent.
    surface = impression.get("surface", "")
    efficiency = 1.0
    if surface in _REELS_SURFACES:
        efficiency = float(params.get("reels_vs_feed_efficiency_ratio", _FALLBACK_REELS_EFFICIENCY))
    ecpm = _sample_ecpm(curve, rng, efficiency=efficiency)

    # Conversion lift propagation (§11.4): test_04 anchors a +3% GEM
    # rollout proxy.  Default 1.0 → no effect; values >1 propagate the
    # lift one-to-one into ad_revenue.
    conversion_lift = float(params.get("conversion_lift_propagation", _FALLBACK_CONVERSION_LIFT))

    # Revenue per single impression: eCPM is "per 1000 impressions".
    ad_revenue_usd = (ecpm / 1000.0) * conversion_lift

    seq = int(impression.get("intra_day_seq", 0)) + 1
    return {
        "scenario_id": impression.get("scenario_id"),
        "tick_day": impression.get("tick_day"),
        "intra_day_seq": seq,
        "timestamp": impression.get("timestamp"),
        "event_type": "ad_impression",
        "viewer_id": impression.get("viewer_id"),
        "viewer_segment": segment,
        "viewer_geo": impression.get("viewer_geo"),
        "viewer_tenure_days": impression.get("viewer_tenure_days"),
        "reel_id": impression.get("reel_id"),
        "ad_impression": 1,
        "ad_revenue_usd": ad_revenue_usd,
        "payload": {"ecpm_usd": round(ecpm, 4)},
    }


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------

def _resolve_curve(segment: Any, params: Mapping[str, Any]) -> dict[str, float]:
    """Pick the per-segment curve if present, else the top-level defaults."""
    by_segment = params.get("by_segment") or {}
    block: Mapping[str, Any] = by_segment.get(segment) or params

    return {
        "ecpm_mean":    float(block.get("ecpm_mean",    _FALLBACK_ECPM_MEAN)),
        "ecpm_sd":      float(block.get("ecpm_sd",      _FALLBACK_ECPM_SD)),
        "ecpm_floor":   float(block.get("ecpm_floor",   _FALLBACK_ECPM_FLOOR)),
        "ecpm_ceiling": float(block.get("ecpm_ceiling", _FALLBACK_ECPM_CEILING)),
    }


def _sample_ecpm(
    curve: Mapping[str, float],
    rng: random.Random,
    *,
    efficiency: float = 1.0,
) -> float:
    mean = curve["ecpm_mean"]
    sd = max(0.0, curve["ecpm_sd"])
    floor = curve["ecpm_floor"]
    ceiling = curve["ecpm_ceiling"]
    if ceiling < floor:
        ceiling = floor

    raw = rng.gauss(mean, sd) if sd > 0.0 else mean
    raw *= float(efficiency)
    if raw < floor:
        return floor
    if raw > ceiling:
        return ceiling
    return raw
