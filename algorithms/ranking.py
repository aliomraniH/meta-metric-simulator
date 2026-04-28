"""Ranking algorithm — pure function, agent-free.

Per docs/ALGORITHMS.md §ranking, this module does ONE thing: given a
viewer state and a candidate pool of reels, score each candidate and
return a top-N ScoredReel list ordered by score.  It does NOT emit
impression events — the engine (M7) creates impressions from this list.

House rules (algorithms/ contract):
  * Pure function.  No module-level rng, no disk I/O, no logging.
  * `rng: random.Random` is injected.  Determinism depends on it.
  * Reads weights / pool-split / top_n from the params dict argument.
    The engine loads params/ranking_weights.yaml and threads it in.
  * Does NOT import from baselines/, engine/, metrics/, or other algorithms.

Pool split
----------
Each reel in `candidate_pool` carries a `pool` field set by the engine
(`"connected"` or `"unconnected"`).  This algorithm scores within each
bucket and produces a top-N list whose composition matches the params'
`pool_split` ratio (default {connected: 0.35, unconnected: 0.65}).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Mapping


# -----------------------------------------------------------------------------
# Public dataclass returned to the engine
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoredReel:
    """One candidate ranked for one viewer.

    `components` is a per-feature contribution map (weight × feature_value)
    used by tests and by the metric layer when explaining a ranking.
    The sum of `components.values()` equals `score` to within floating
    point tolerance.
    """

    reel_id: str
    score: float
    pool: str
    components: dict[str, float] = field(default_factory=dict)
    # Carried through so the engine can build impression events without
    # re-joining against the candidate pool.
    creator_id: str | None = None
    creator_tier: str | None = None
    reel_duration_sec: float | None = None
    reel_topic_cluster: str | None = None


# -----------------------------------------------------------------------------
# Defaults — overridable via params dict
# -----------------------------------------------------------------------------

DEFAULT_TOP_N = 10
DEFAULT_POOL_SPLIT = {"connected": 0.35, "unconnected": 0.65}

# Feature names the ranker knows.  Anything not in this set is silently
# skipped — params files declare which features to weight.
_KNOWN_FEATURES: frozenset[str] = frozenset({
    "predicted_watch_time",
    "predicted_engagement",
    "predicted_send",
    "topic_affinity",
    "creator_tier_bonus",
    "novelty",
    "integrity_score",
})

# Tie-breaker noise magnitude — small enough that it never overrides a
# real score difference but enough to give identical scores a stable
# random order under a fixed seed.
_TIE_BREAKER_MAG = 1e-9


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def rank(
    viewer_state: Mapping[str, Any],
    candidate_pool: list[Mapping[str, Any]],
    params: Mapping[str, Any],
    rng: random.Random,
) -> list[ScoredReel]:
    """Score candidates for a viewer and return the top-N ScoredReel list.

    Args:
        viewer_state: ViewerState dict per ALGORITHMS.md.  Required keys:
            `viewer_id`, `segment`.  Optional: `recent_topic_clusters`,
            `tier_preferences`, `fatigue`.
        candidate_pool: list of Reel dicts, each with `reel_id` and
            `pool` ∈ {"connected", "unconnected"}.  The engine pre-splits
            the pools; the ranker honours the params' ratio.
        params: ranking_weights.yaml sub-dict.  Recognised keys:
            `weights` (feature → float), `pool_split`, `top_n`.
        rng: injected random.Random for tie-breaking.

    Returns:
        list[ScoredReel] sorted by score desc, length ≤ params.top_n.
        Connected/unconnected ratio matches `params.pool_split`.
    """
    weights = dict(params.get("weights", {}))
    top_n = int(params.get("top_n", DEFAULT_TOP_N))
    pool_split = dict(params.get("pool_split", DEFAULT_POOL_SPLIT))

    # Score every candidate in its own bucket.
    by_pool: dict[str, list[ScoredReel]] = {"connected": [], "unconnected": []}
    for reel in candidate_pool:
        pool = reel.get("pool", "unconnected")
        if pool not in by_pool:
            # Unknown pool labels go to "unconnected" so unrecognised
            # values don't silently drop reels.
            pool = "unconnected"
        scored = _score_one(viewer_state, reel, weights, pool, rng)
        by_pool[pool].append(scored)

    # Sort each bucket; tie-breaker noise on `score` already shuffles ties.
    for bucket in by_pool.values():
        bucket.sort(key=lambda r: r.score, reverse=True)

    # Apportion top_n across buckets per pool_split.  We round per bucket
    # and absorb any rounding gap into the larger bucket so totals match.
    quota_connected = int(round(top_n * pool_split.get("connected", 0.0)))
    quota_unconnected = top_n - quota_connected

    # If a bucket runs short, fill from the other bucket so the engine
    # always sees up to top_n results when reels exist.
    chosen_connected = by_pool["connected"][:quota_connected]
    chosen_unconnected = by_pool["unconnected"][:quota_unconnected]
    deficit = quota_connected - len(chosen_connected)
    if deficit > 0:
        chosen_unconnected += by_pool["unconnected"][
            quota_unconnected : quota_unconnected + deficit
        ]
    deficit = quota_unconnected - len(chosen_unconnected)
    if deficit > 0:
        chosen_connected += by_pool["connected"][
            quota_connected : quota_connected + deficit
        ]

    combined = chosen_connected + chosen_unconnected
    combined.sort(key=lambda r: r.score, reverse=True)
    return combined


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------

def _score_one(
    viewer_state: Mapping[str, Any],
    reel: Mapping[str, Any],
    weights: Mapping[str, float],
    pool: str,
    rng: random.Random,
) -> ScoredReel:
    features = _features_for(viewer_state, reel, pool)

    components: dict[str, float] = {}
    total = 0.0
    for feat_name, value in features.items():
        if feat_name not in weights:
            continue
        contrib = float(weights[feat_name]) * float(value)
        components[feat_name] = contrib
        total += contrib

    # Stable random tie-breaker — small enough never to override a real
    # weight difference at typical magnitudes.
    jitter = (rng.random() - 0.5) * _TIE_BREAKER_MAG
    components["_tie_breaker"] = jitter
    total += jitter

    return ScoredReel(
        reel_id=str(reel["reel_id"]),
        score=total,
        pool=pool,
        components=components,
        creator_id=reel.get("creator_id"),
        creator_tier=reel.get("creator_tier"),
        reel_duration_sec=_as_float(reel.get("duration_sec")),
        reel_topic_cluster=reel.get("topic_cluster"),
    )


def _features_for(
    viewer_state: Mapping[str, Any],
    reel: Mapping[str, Any],
    pool: str,
) -> dict[str, float]:
    """Compute the feature vector for a (viewer, reel) pair.

    Features are normalised to roughly the [0, 1] range so weights are
    comparable across the params yaml.  Missing reel fields default to
    a neutral middle value, never raising.
    """
    duration = _as_float(reel.get("duration_sec")) or 15.0
    # Watch-time prediction: shorter reels get watched more reliably,
    # but cap the bonus at 30s — beyond that, completion likelihood drops.
    predicted_watch_time = max(0.0, min(1.0, 1.0 - abs(duration - 15.0) / 30.0))

    novelty = _clamp01(reel.get("novelty", 0.5))
    integrity = _clamp01(reel.get("integrity_score", 0.95))
    predicted_engagement = _clamp01(reel.get("predicted_engagement", 0.10))
    predicted_send = _clamp01(reel.get("predicted_send", 0.02))

    # Topic affinity: 1.0 if the reel's topic_cluster appears in the
    # viewer's recent_topic_clusters, scaled by recency.  Empty list → 0.5
    # neutral (we don't penalise a cold start).
    topic_affinity = _topic_affinity(
        viewer_state.get("recent_topic_clusters") or [],
        reel.get("topic_cluster"),
    )

    # Creator tier bonus: pulled from viewer's tier_preferences if present;
    # default 0.5 (neutral).
    creator_tier_bonus = _clamp01(
        (viewer_state.get("tier_preferences") or {}).get(
            reel.get("creator_tier"),
            0.5,
        )
    )

    # The `pool` argument is informational here; weights for connected vs
    # unconnected belong in the pool_split ratio, not in feature space.
    _ = pool

    return {
        "predicted_watch_time": predicted_watch_time,
        "predicted_engagement": predicted_engagement,
        "predicted_send": predicted_send,
        "topic_affinity": topic_affinity,
        "creator_tier_bonus": creator_tier_bonus,
        "novelty": novelty,
        "integrity_score": integrity,
    }


def _topic_affinity(recent: list[str], topic: Any) -> float:
    if not recent or topic is None:
        return 0.5
    if topic in recent:
        # Most recent (last in list) gets 1.0; older entries decay linearly.
        idx = len(recent) - 1 - recent[::-1].index(topic)
        recency = idx / max(1, len(recent) - 1) if len(recent) > 1 else 1.0
        return _clamp01(0.6 + 0.4 * recency)
    return 0.3


def _clamp01(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.5
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _as_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
