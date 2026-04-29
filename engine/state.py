"""WorldState — the simulation's mutable per-tick state.

The engine threads one WorldState through every tick.  It carries the
viewer / creator / reel populations, the integrity prevalence, the
active perturbation list, the running tick_day, and the seeded rng that
is the ONLY source of randomness in the simulator.

Determinism contract
--------------------
Same (scenario.seed, scenario.perturbations, params snapshot, population
sizes) → bit-identical event sequences (compared on the substrate
ordering tuple, not on event_ids).  The rng is mutated in place across
calls to algorithms; that's intentional — the determinism comes from
the rng having a known seed AND being the sole randomness source.

Population sizes
----------------
DEFAULT_VIEWERS_N / DEFAULT_CREATORS_N / DEFAULT_REELS_N are sensible
production defaults.  Tests override via WorldState.bootstrap kwargs.
A future M5-followup may add a `simulation_population.viewers_n` field
to the params/segment_propensities.yaml _meta block; until then the
defaults below are the source of truth.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Mapping


# Production defaults — tests pass smaller values via bootstrap kwargs.
DEFAULT_VIEWERS_N = 1000
DEFAULT_CREATORS_N = 100
DEFAULT_REELS_N = 500


# Reel-pool generation parameters (used by tick.py).  Surfaced here so
# the engine has one place to look.
DEFAULT_CANDIDATE_POOL_SIZE = 50
DEFAULT_IMPRESSIONS_PER_VIEWER_PER_TICK = 5


@dataclass
class WorldState:
    tick_day: int
    viewers: dict[str, dict[str, Any]]
    creators: dict[str, dict[str, Any]]
    reels: dict[str, dict[str, Any]]
    integrity_prevalence: dict[str, Any]
    active_perturbations: list[dict[str, Any]]
    rng: random.Random
    # Tick-scoped scratchpad the engine writes into during run_tick.
    # Carries `last_tick_earnings_by_creator` between ticks so creator
    # supply can respond on a lag.
    scratch: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def bootstrap(
        cls,
        *,
        seed: int,
        params: Mapping[str, Mapping[str, Any]],
        viewers_n: int = DEFAULT_VIEWERS_N,
        creators_n: int = DEFAULT_CREATORS_N,
        reels_n: int = DEFAULT_REELS_N,
    ) -> "WorldState":
        """Construct a fresh WorldState from the seed and loaded params.

        Population sizes are kwargs (not read from scenario) because the
        scenario should not change determinism for a given seed — fixing
        sizes here keeps the contract clean.
        """
        rng = random.Random(seed)
        viewers = _make_viewers(viewers_n, params, rng)
        creators = _make_creators(creators_n, params, rng)
        reels = _make_reels(reels_n, list(creators.keys()), params, rng)
        integrity_prevalence = _initial_prevalence(params)

        return cls(
            tick_day=0,
            viewers=viewers,
            creators=creators,
            reels=reels,
            integrity_prevalence=integrity_prevalence,
            active_perturbations=[],
            rng=rng,
            scratch={"last_tick_earnings_by_creator": {}},
        )


# -----------------------------------------------------------------------------
# Bootstrap helpers — pure functions of (params, rng)
# -----------------------------------------------------------------------------

def _segment_distribution(params: Mapping[str, Any]) -> list[tuple[str, float]]:
    """Return [(segment_name, weight)] from params/segment_propensities.yaml.

    Weights derive from per-segment population_share_pct.  We renormalise
    so the weights sum to 1 (the yaml may not sum to exactly 100 because
    of synthesized seed values).
    """
    segs = (params.get("segment_propensities") or {}).get("segments") or {}
    pairs: list[tuple[str, float]] = []
    for name, block in segs.items():
        share = float((block.get("population_share_pct") or {}).get("value", 0.0))
        if share > 0:
            pairs.append((name, share))
    total = sum(w for _, w in pairs) or 1.0
    return [(n, w / total) for n, w in pairs]


def _make_viewers(
    n: int,
    params: Mapping[str, Any],
    rng: random.Random,
) -> dict[str, dict[str, Any]]:
    distribution = _segment_distribution(params) or [("snackers", 1.0)]
    seg_names = [s for s, _ in distribution]
    seg_weights = [w for _, w in distribution]

    viewers: dict[str, dict[str, Any]] = {}
    for i in range(n):
        segment = rng.choices(seg_names, weights=seg_weights, k=1)[0]
        viewer_id = f"v-{i:05d}"
        viewers[viewer_id] = {
            "viewer_id": viewer_id,
            "segment": segment,
            "geo": rng.choice(["US", "BR", "IN", "ID", "DE", "GB"]),
            "tenure_days": rng.randint(1, 1825),
            "recent_topic_clusters": [],
            "tier_preferences": {"head": 0.7, "mid": 0.5, "long_tail": 0.4},
            "fatigue": 0.0,
        }
    return viewers


def _tier_weights(params: Mapping[str, Any]) -> list[tuple[str, float]]:
    """Return [(tier_name, weight)] from creator_economics tier_distribution_pct.

    The HypeAuditor distribution has overlapping tiers (sums non-100); we
    renormalise to a probability distribution.
    """
    block = (params.get("creator_economics") or {}).get("tier_distribution_pct") or {}
    pairs: list[tuple[str, float]] = []
    # The yaml uses keys like nano_75_9 / micro_27_7 / mid_6_4 / macro_mega_0_23.
    name_map = {
        "nano_75_9": "nano",
        "micro_27_7": "micro",
        "mid_6_4": "mid",
        "macro_mega_0_23": "macro",
    }
    for raw_key, sub in block.items():
        tier = name_map.get(raw_key, raw_key)
        weight = float(sub.get("value", 0.0))
        if weight > 0:
            pairs.append((tier, weight))
    if not pairs:
        pairs = [("nano", 1.0)]
    total = sum(w for _, w in pairs) or 1.0
    return [(n, w / total) for n, w in pairs]


def _make_creators(
    n: int,
    params: Mapping[str, Any],
    rng: random.Random,
) -> dict[str, dict[str, Any]]:
    weights = _tier_weights(params)
    tier_names = [t for t, _ in weights]
    tier_weights = [w for _, w in weights]

    economics = params.get("creator_economics") or {}
    floor = float((economics.get("posting_floor_per_day") or {}).get("value", 0.05))
    ceiling = float((economics.get("posting_ceiling_per_day") or {}).get("value", 5.0))

    creators: dict[str, dict[str, Any]] = {}
    for i in range(n):
        tier = rng.choices(tier_names, weights=tier_weights, k=1)[0]
        creator_id = f"c-{i:05d}"
        baseline_rate = rng.uniform(floor, min(ceiling, 2.0))
        creators[creator_id] = {
            "creator_id": creator_id,
            "tier": tier,
            "posting_rate_per_day": baseline_rate,
            "baseline_posting_rate": baseline_rate,
            "baseline_earnings": rng.uniform(1.0, 50.0),
            "earnings_history": [],
        }
    return creators


def _make_reels(
    n: int,
    creator_ids: list[str],
    params: Mapping[str, Any],
    rng: random.Random,
) -> dict[str, dict[str, Any]]:
    if not creator_ids:
        creator_ids = ["c-00000"]
    topics = ["music", "comedy", "sports", "news", "fashion", "food", "gaming", "education"]
    durations = [10.0, 15.0, 18.0, 22.0, 30.0, 45.0, 60.0]

    # creator → tier lookup so reels carry the right tier label.
    # Must match the creators dict the same bootstrap call built; the
    # caller passes creator_ids in order so we don't need the dict.
    reels: dict[str, dict[str, Any]] = {}
    for i in range(n):
        creator_id = rng.choice(creator_ids)
        reel_id = f"r-{i:06d}"
        reels[reel_id] = {
            "reel_id": reel_id,
            "creator_id": creator_id,
            "creator_tier": rng.choice(["head", "mid", "long_tail"]),
            "duration_sec": rng.choice(durations),
            "topic_cluster": rng.choice(topics),
            "novelty": rng.random(),
            "integrity_score": 0.95 + rng.random() * 0.05,
            "predicted_engagement": rng.uniform(0.05, 0.25),
            "predicted_send": rng.uniform(0.005, 0.05),
            "pool": "connected" if rng.random() < 0.35 else "unconnected",
        }
    return reels


def _initial_prevalence(params: Mapping[str, Any]) -> dict[str, Any]:
    """Seed the prevalence_state used by integrity_dynamics."""
    integrity = params.get("integrity_dynamics") or {}
    detection = float((integrity.get("detection_rate_pct") or {}).get("value", 95.0)) / 100.0
    return {
        "violating_prevalence": 0.0003,  # ~3× IG baseline; small-but-nonzero seed
        "detection_rate": detection,
        "by_topic": {},
    }
