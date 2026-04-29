"""Unit tests for algorithms/monetization.py.

Per the M4b contract:
  1. eCPM samples stay within the configured [floor, ceiling].
  2. Mean of N=10000 samples converges toward params.ecpm_mean (within 5%).
  3. ad_load_policy.probability is honoured (frequency of non-None returns).
  4. Determinism with a fixed seed.
  5. Emitted events pass the substrate writer's structural validation.
"""
from __future__ import annotations

import random
import statistics

from algorithms.monetization import maybe_ad
from substrate.writer import _prepare


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

def _impression(viewer_segment: str = "young_adult") -> dict:
    # surface="feed" so the M11.5 Reels-vs-Feed efficiency multiplier does
    # NOT apply to these unit tests — they verify pure eCPM sampling.
    # Reels-surface behaviour is exercised end-to-end by the calibration
    # runner.
    return {
        "scenario_id": "scn-mon",
        "tick_day": 5,
        "intra_day_seq": 50,
        "timestamp": 432000.0,
        "event_type": "impression",
        "viewer_id": "v-1",
        "viewer_segment": viewer_segment,
        "viewer_geo": "US",
        "viewer_tenure_days": 120,
        "creator_id": "c-1",
        "creator_tier": "head",
        "reel_id": "r-ad-slot",
        "surface": "feed",
        "pool": "unconnected",
        "rank_score": 0.5,
    }


def _params() -> dict:
    return {
        "ecpm_mean": 8.0,
        "ecpm_sd": 2.0,
        "ecpm_floor": 0.5,
        "ecpm_ceiling": 30.0,
        "by_segment": {
            "teen": {
                "ecpm_mean": 4.0,
                "ecpm_sd": 1.0,
                "ecpm_floor": 0.25,
                "ecpm_ceiling": 12.0,
            },
        },
    }


# -----------------------------------------------------------------------------
# 1. eCPM stays within configured [floor, ceiling]
# -----------------------------------------------------------------------------

def test_ecpm_samples_within_floor_and_ceiling():
    params = _params()
    policy = {"probability": 1.0}  # force every slot to be an ad
    revenues: list[float] = []
    for i in range(2000):
        ev = maybe_ad(_impression(), policy, params, random.Random(i))
        assert ev is not None
        ecpm = ev["payload"]["ecpm_usd"]
        assert params["ecpm_floor"] <= ecpm <= params["ecpm_ceiling"]
        revenues.append(ev["ad_revenue_usd"])
    # ad_revenue_usd = ecpm / 1000, so each value should also be in range.
    assert all(params["ecpm_floor"] / 1000 <= r <= params["ecpm_ceiling"] / 1000 for r in revenues)


def test_ecpm_clamped_when_distribution_centre_above_ceiling():
    """Pathological config where mean ≫ ceiling — every sample clamps to ceiling."""
    params = {"ecpm_mean": 100.0, "ecpm_sd": 5.0, "ecpm_floor": 0.5, "ecpm_ceiling": 10.0}
    policy = {"probability": 1.0}
    for i in range(50):
        ev = maybe_ad(_impression(), policy, params, random.Random(i))
        assert ev["payload"]["ecpm_usd"] == 10.0


# -----------------------------------------------------------------------------
# 2. Mean convergence
# -----------------------------------------------------------------------------

def test_ecpm_mean_within_5pct_for_n_10000():
    params = _params()
    policy = {"probability": 1.0}
    samples: list[float] = []
    for i in range(10_000):
        ev = maybe_ad(_impression(), policy, params, random.Random(i))
        samples.append(ev["payload"]["ecpm_usd"])
    observed = statistics.fmean(samples)
    expected = params["ecpm_mean"]
    pct_diff = abs(observed - expected) / expected
    assert pct_diff < 0.05, f"observed mean {observed:.3f} vs expected {expected} ({pct_diff:.1%} diff)"


# -----------------------------------------------------------------------------
# 3. ad_load_policy probability honoured
# -----------------------------------------------------------------------------

def test_probability_zero_never_returns_an_ad():
    params = _params()
    policy = {"probability": 0.0}
    for i in range(500):
        assert maybe_ad(_impression(), policy, params, random.Random(i)) is None


def test_probability_one_always_returns_an_ad():
    params = _params()
    policy = {"probability": 1.0}
    for i in range(500):
        assert maybe_ad(_impression(), policy, params, random.Random(i)) is not None


def test_probability_frequency_matches_policy():
    params = _params()
    policy = {"probability": 0.25}
    n = 10_000
    ads = sum(
        1
        for i in range(n)
        if maybe_ad(_impression(), policy, params, random.Random(i)) is not None
    )
    rate = ads / n
    assert 0.225 < rate < 0.275, f"observed rate {rate:.4f} outside ±10% of 0.25"


# -----------------------------------------------------------------------------
# 4. Determinism
# -----------------------------------------------------------------------------

def test_maybe_ad_deterministic_under_fixed_seed():
    params = _params()
    policy = {"probability": 0.5}
    a = maybe_ad(_impression(), policy, params, random.Random(123))
    b = maybe_ad(_impression(), policy, params, random.Random(123))
    assert a == b


# -----------------------------------------------------------------------------
# 5. Per-segment override + structural validation
# -----------------------------------------------------------------------------

def test_per_segment_curve_overrides_global():
    params = _params()  # teen has tighter range than the default
    policy = {"probability": 1.0}
    teen_samples: list[float] = []
    for i in range(2000):
        ev = maybe_ad(_impression("teen"), policy, params, random.Random(i))
        teen_samples.append(ev["payload"]["ecpm_usd"])
    teen_mean = statistics.fmean(teen_samples)
    teen_max = max(teen_samples)
    # Teen ceiling is 12; teen mean ≈ 4.
    assert teen_max <= 12.0
    assert 3.5 < teen_mean < 4.5


def test_emitted_event_passes_substrate_writer_validation():
    params = _params()
    policy = {"probability": 1.0}
    ev = maybe_ad(_impression(), policy, params, random.Random(0))
    prepared = _prepare(ev)
    assert prepared["event_type"] == "ad_impression"
    assert prepared["ad_impression"] == 1
    assert prepared["ad_revenue_usd"] >= 0.0
    assert prepared["viewer_id"] == "v-1"


def test_emitted_event_revenue_matches_ecpm():
    """ad_revenue_usd is ecpm / 1000.  payload.ecpm_usd is rounded to 4dp
    for readability so the tolerance only needs to be tighter than that."""
    params = _params()
    policy = {"probability": 1.0}
    for i in range(50):
        ev = maybe_ad(_impression(), policy, params, random.Random(i))
        # rounding to 4dp gives a ±5e-5 / 1000 = 5e-8 tolerance budget.
        assert abs(ev["ad_revenue_usd"] - ev["payload"]["ecpm_usd"] / 1000.0) < 1e-6
