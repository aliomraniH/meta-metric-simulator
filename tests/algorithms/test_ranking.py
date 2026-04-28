"""Unit tests for algorithms/ranking.py.

Pins the four behaviours the M4a contract requires:
  1. Determinism under a fixed rng seed.
  2. Weight perturbation produces the expected ordering shifts.
  3. Connected/unconnected pool split is honoured.
  4. The `components` dict on each ScoredReel is consistent with the
     final score (sums to it, modulo the tiny tie-breaker jitter).
"""
from __future__ import annotations

import random

from algorithms.ranking import ScoredReel, rank


# -----------------------------------------------------------------------------
# Fixtures (lightweight — these stay inside this file by design)
# -----------------------------------------------------------------------------

def _viewer() -> dict:
    return {
        "viewer_id": "v-1",
        "segment": "young_adult",
        "recent_topic_clusters": ["music", "comedy", "music"],
        "tier_preferences": {"head": 0.7, "mid": 0.5, "long_tail": 0.4},
    }


def _candidates() -> list[dict]:
    """A small mix that exercises every feature and both pools."""
    return [
        {
            "reel_id": "r-A", "creator_id": "c-1", "creator_tier": "head",
            "duration_sec": 15.0, "topic_cluster": "music",
            "predicted_engagement": 0.20, "predicted_send": 0.04,
            "novelty": 0.6, "integrity_score": 0.99, "pool": "connected",
        },
        {
            "reel_id": "r-B", "creator_id": "c-2", "creator_tier": "mid",
            "duration_sec": 30.0, "topic_cluster": "sports",
            "predicted_engagement": 0.10, "predicted_send": 0.01,
            "novelty": 0.5, "integrity_score": 0.95, "pool": "connected",
        },
        {
            "reel_id": "r-C", "creator_id": "c-3", "creator_tier": "long_tail",
            "duration_sec": 12.0, "topic_cluster": "comedy",
            "predicted_engagement": 0.15, "predicted_send": 0.02,
            "novelty": 0.8, "integrity_score": 0.97, "pool": "unconnected",
        },
        {
            "reel_id": "r-D", "creator_id": "c-4", "creator_tier": "long_tail",
            "duration_sec": 20.0, "topic_cluster": "news",
            "predicted_engagement": 0.05, "predicted_send": 0.005,
            "novelty": 0.4, "integrity_score": 0.92, "pool": "unconnected",
        },
        {
            "reel_id": "r-E", "creator_id": "c-5", "creator_tier": "mid",
            "duration_sec": 18.0, "topic_cluster": "music",
            "predicted_engagement": 0.18, "predicted_send": 0.03,
            "novelty": 0.7, "integrity_score": 0.98, "pool": "unconnected",
        },
        {
            "reel_id": "r-F", "creator_id": "c-6", "creator_tier": "head",
            "duration_sec": 10.0, "topic_cluster": "fashion",
            "predicted_engagement": 0.12, "predicted_send": 0.02,
            "novelty": 0.55, "integrity_score": 0.96, "pool": "unconnected",
        },
    ]


def _balanced_params(top_n: int = 4) -> dict:
    return {
        "weights": {
            "predicted_watch_time": 1.0,
            "predicted_engagement": 1.0,
            "predicted_send": 1.0,
            "topic_affinity": 1.0,
            "creator_tier_bonus": 0.5,
            "novelty": 0.3,
            "integrity_score": 0.2,
        },
        "pool_split": {"connected": 0.5, "unconnected": 0.5},
        "top_n": top_n,
    }


# -----------------------------------------------------------------------------
# 1. Determinism
# -----------------------------------------------------------------------------

def test_rank_is_deterministic_under_fixed_seed():
    viewer = _viewer()
    pool = _candidates()
    params = _balanced_params(top_n=6)

    a = rank(viewer, pool, params, random.Random(42))
    b = rank(viewer, pool, params, random.Random(42))

    assert [r.reel_id for r in a] == [r.reel_id for r in b]
    for ra, rb in zip(a, b):
        assert ra.score == rb.score
        assert ra.components == rb.components


def test_different_seeds_only_break_ties_not_real_differences():
    """Tie-breaker noise is 1e-9 so substantive score gaps survive any seed."""
    viewer = _viewer()
    pool = _candidates()
    params = _balanced_params(top_n=6)

    seen_top1: set[str] = set()
    for seed in range(20):
        r = rank(viewer, pool, params, random.Random(seed))
        seen_top1.add(r[0].reel_id)
    # The clear winner should be the same across all 20 seeds — tie-breaker
    # magnitude (1e-9) cannot flip a meaningful score gap.
    assert len(seen_top1) == 1, f"top-1 unstable across seeds: {seen_top1}"


# -----------------------------------------------------------------------------
# 2. Weight perturbation produces expected ordering shifts
# -----------------------------------------------------------------------------

def test_raising_engagement_weight_promotes_engagement_heavy_reel():
    viewer = _viewer()
    pool = _candidates()
    rng = random.Random(0)

    # Baseline: very low engagement weight.  r-A (highest predicted_engagement
    # of 0.20) should not necessarily be on top.
    low = dict(_balanced_params(top_n=6))
    low["weights"] = {**low["weights"], "predicted_engagement": 0.01}
    base_order = [r.reel_id for r in rank(viewer, pool, low, random.Random(0))]

    # Heavy: huge engagement weight.  r-A must rank above any reel with a
    # lower predicted_engagement.
    heavy = dict(_balanced_params(top_n=6))
    heavy["weights"] = {**heavy["weights"], "predicted_engagement": 100.0}
    heavy_order = [r.reel_id for r in rank(viewer, pool, heavy, random.Random(0))]

    # r-A has predicted_engagement=0.20 (highest); under the heavy weight it
    # should be the top-1 result.
    assert heavy_order[0] == "r-A"
    # And it should rank no worse than its baseline position.
    assert heavy_order.index("r-A") <= base_order.index("r-A")


def test_raising_topic_affinity_weight_promotes_matching_topic():
    """Viewer's recent_topic_clusters has 'music'; r-A and r-E are music."""
    viewer = _viewer()
    pool = _candidates()

    params = _balanced_params(top_n=6)
    params["weights"] = {
        "predicted_watch_time": 0.0,
        "predicted_engagement": 0.0,
        "predicted_send": 0.0,
        "topic_affinity": 100.0,
        "creator_tier_bonus": 0.0,
        "novelty": 0.0,
        "integrity_score": 0.0,
    }
    result = rank(viewer, pool, params, random.Random(0))
    top_two = {r.reel_id for r in result[:2]}
    assert top_two == {"r-A", "r-E"}, f"got top-2 {top_two}"


# -----------------------------------------------------------------------------
# 3. Pool split is honoured
# -----------------------------------------------------------------------------

def test_pool_split_50_50_with_top_n_4():
    viewer = _viewer()
    pool = _candidates()
    params = _balanced_params(top_n=4)
    params["pool_split"] = {"connected": 0.5, "unconnected": 0.5}

    result = rank(viewer, pool, params, random.Random(0))
    assert len(result) == 4
    pools = [r.pool for r in result]
    assert pools.count("connected") == 2
    assert pools.count("unconnected") == 2


def test_pool_split_35_65_with_top_n_10():
    viewer = _viewer()
    # Build a pool with at least 4 connected and 6 unconnected so neither
    # bucket forces the deficit-fill fallback.
    pool = _candidates()  # 2 connected, 4 unconnected
    pool += [
        {**pool[0], "reel_id": "r-A2", "pool": "connected"},
        {**pool[0], "reel_id": "r-A3", "pool": "connected"},
        {**pool[2], "reel_id": "r-C2", "pool": "unconnected"},
        {**pool[2], "reel_id": "r-C3", "pool": "unconnected"},
    ]
    params = _balanced_params(top_n=10)
    params["pool_split"] = {"connected": 0.35, "unconnected": 0.65}

    result = rank(viewer, pool, params, random.Random(0))
    pools = [r.pool for r in result]
    # round(10 * 0.35) = 4 connected, 6 unconnected.
    assert pools.count("connected") == 4
    assert pools.count("unconnected") == 6


def test_pool_split_falls_back_when_one_bucket_short():
    """If connected pool only has 1 reel, the deficit is filled from unconnected."""
    viewer = _viewer()
    pool = [
        {"reel_id": "r-only-conn", "pool": "connected", "duration_sec": 15,
         "predicted_engagement": 0.2, "topic_cluster": "music"},
    ]
    pool += [
        {"reel_id": f"r-u-{i}", "pool": "unconnected", "duration_sec": 15,
         "predicted_engagement": 0.1, "topic_cluster": "comedy"}
        for i in range(5)
    ]
    params = _balanced_params(top_n=4)
    params["pool_split"] = {"connected": 0.5, "unconnected": 0.5}

    result = rank(viewer, pool, params, random.Random(0))
    assert len(result) == 4
    # Only 1 connected reel exists; the other 3 must be unconnected.
    assert sum(1 for r in result if r.pool == "connected") == 1
    assert sum(1 for r in result if r.pool == "unconnected") == 3


# -----------------------------------------------------------------------------
# 4. components dict consistency
# -----------------------------------------------------------------------------

def test_components_sum_to_score_within_tolerance():
    viewer = _viewer()
    pool = _candidates()
    params = _balanced_params(top_n=6)

    result = rank(viewer, pool, params, random.Random(7))
    for r in result:
        s = sum(r.components.values())
        assert abs(s - r.score) < 1e-9, (
            f"reel {r.reel_id}: components sum {s} vs score {r.score} "
            f"(diff {s - r.score})"
        )


def test_components_lists_only_weighted_features_plus_tie_breaker():
    """If a feature has zero weight, its contribution is omitted from components.

    The tie-breaker entry is always present.
    """
    viewer = _viewer()
    pool = _candidates()
    params = _balanced_params(top_n=6)
    # Drop predicted_send from weights entirely.
    params["weights"] = {k: v for k, v in params["weights"].items() if k != "predicted_send"}

    result = rank(viewer, pool, params, random.Random(0))
    for r in result:
        assert "predicted_send" not in r.components
        assert "_tie_breaker" in r.components


def test_returns_scoredreel_dataclass_with_required_fields():
    viewer = _viewer()
    pool = _candidates()
    params = _balanced_params(top_n=2)

    result = rank(viewer, pool, params, random.Random(0))
    assert all(isinstance(r, ScoredReel) for r in result)
    for r in result:
        assert r.reel_id
        assert r.pool in {"connected", "unconnected"}
        assert isinstance(r.components, dict)
        assert isinstance(r.score, float)
