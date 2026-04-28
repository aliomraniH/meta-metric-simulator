"""Unit tests for algorithms/engagement_response.py.

Per the M4b contract:
  1. Segment propensity differences produce expected behaviour deltas
     (snackers skip more than deep-watchers given the same impression).
  2. Skip threshold is honoured: a `skip` event has watch_duration_sec
     ≤ skip_threshold; a non-skip event has watch_duration_sec ≥ threshold.
  3. Determinism under a fixed seed.
  4. Emitted events match the EVENT_SCHEMA.md required fields per type
     (we use substrate.writer._prepare as the structural validator).
"""
from __future__ import annotations

import random
from collections import Counter

import pytest

from algorithms.engagement_response import respond
from substrate.writer import EventValidationError, _prepare


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

def _impression(viewer_segment: str = "snacker", reel_duration: float = 18.0) -> dict:
    return {
        "scenario_id": "scn-eng",
        "tick_day": 2,
        "intra_day_seq": 100,
        "timestamp": 7200.0,
        "event_type": "impression",
        "viewer_id": "v-1",
        "viewer_segment": viewer_segment,
        "viewer_geo": "US",
        "viewer_tenure_days": 90,
        "creator_id": "c-7",
        "creator_tier": "head",
        "reel_id": "r-42",
        "reel_duration_sec": reel_duration,
        "reel_topic_cluster": "music",
        "surface": "reels_tab",
        "pool": "connected",
        "rank_score": 0.81,
    }


def _viewer_state(segment: str = "snacker") -> dict:
    return {"viewer_id": "v-1", "segment": segment, "geo": "US", "tenure_days": 90}


def _params() -> dict:
    """Two contrasting segments — high-skip snacker vs low-skip deep_watcher."""
    return {
        "segment_propensities": {
            "snacker": {
                "skip_threshold_sec": 3.0,
                "p_skip": 0.65,
                "completion_pct_min": 0.50,
                "completion_pct_max": 0.80,
                "view_end_threshold_pct": 0.95,
                "like_propensity":    0.04,
                "send_propensity":    0.01,
                "save_propensity":    0.01,
                "comment_propensity": 0.005,
                "replay_propensity":  0.02,
            },
            "deep_watcher": {
                "skip_threshold_sec": 3.0,
                "p_skip": 0.10,
                "completion_pct_min": 0.85,
                "completion_pct_max": 1.00,
                "view_end_threshold_pct": 0.90,
                "like_propensity":    0.20,
                "send_propensity":    0.06,
                "save_propensity":    0.05,
                "comment_propensity": 0.03,
                "replay_propensity":  0.10,
            },
        }
    }


def _types(events: list[dict]) -> list[str]:
    return [e["event_type"] for e in events]


# -----------------------------------------------------------------------------
# 1. Segment propensity differences
# -----------------------------------------------------------------------------

def test_snackers_skip_more_than_deep_watchers():
    params = _params()
    n = 5000
    snacker_skips = 0
    deep_skips = 0
    for i in range(n):
        snacker_evs = respond(
            _viewer_state("snacker"),
            _impression("snacker"),
            params,
            random.Random(i),
        )
        if "skip" in _types(snacker_evs):
            snacker_skips += 1
        deep_evs = respond(
            _viewer_state("deep_watcher"),
            _impression("deep_watcher"),
            params,
            random.Random(i),
        )
        if "skip" in _types(deep_evs):
            deep_skips += 1

    snacker_rate = snacker_skips / n
    deep_rate = deep_skips / n
    assert snacker_rate > deep_rate, (
        f"snackers should skip more (got {snacker_rate:.3f} vs {deep_rate:.3f})"
    )
    # Sanity: rates roughly track the configured p_skip values (loose bounds
    # so this isn't flaky on tiny RNG drift).
    assert 0.55 < snacker_rate < 0.75
    assert 0.05 < deep_rate < 0.15


def test_deep_watchers_engage_more_than_snackers():
    """Deep-watchers should fire more like/send/save chained events."""
    params = _params()
    n = 3000
    snacker_likes = 0
    deep_likes = 0
    for i in range(n):
        for evs, label in (
            (respond(_viewer_state("snacker"),     _impression("snacker"),     params, random.Random(i)), "snacker"),
            (respond(_viewer_state("deep_watcher"), _impression("deep_watcher"), params, random.Random(i)), "deep"),
        ):
            for e in evs:
                if e["event_type"] == "like":
                    if label == "snacker":
                        snacker_likes += 1
                    else:
                        deep_likes += 1
    assert deep_likes > snacker_likes


# -----------------------------------------------------------------------------
# 2. Skip threshold honoured
# -----------------------------------------------------------------------------

def test_skip_event_has_watch_duration_under_threshold():
    params = _params()
    threshold = params["segment_propensities"]["snacker"]["skip_threshold_sec"]
    found = 0
    for i in range(2000):
        evs = respond(_viewer_state("snacker"), _impression("snacker"), params, random.Random(i))
        skips = [e for e in evs if e["event_type"] == "skip"]
        for s in skips:
            assert s["watch_duration_sec"] <= threshold, (
                f"skip with watch={s['watch_duration_sec']} > threshold={threshold}"
            )
            found += 1
    assert found > 0  # the skip path is exercised


def test_non_skip_events_have_watch_duration_at_or_above_threshold():
    params = _params()
    threshold = params["segment_propensities"]["deep_watcher"]["skip_threshold_sec"]
    found = 0
    for i in range(1000):
        evs = respond(_viewer_state("deep_watcher"), _impression("deep_watcher"), params, random.Random(i))
        for e in evs:
            if e["event_type"] in {"watch", "view_end", "replay"}:
                assert e["watch_duration_sec"] >= threshold, (
                    f"{e['event_type']} with watch={e['watch_duration_sec']} < threshold={threshold}"
                )
                found += 1
    assert found > 0


# -----------------------------------------------------------------------------
# 3. Determinism
# -----------------------------------------------------------------------------

def test_respond_deterministic_under_fixed_seed():
    params = _params()
    a = respond(_viewer_state("snacker"), _impression("snacker"), params, random.Random(11))
    b = respond(_viewer_state("snacker"), _impression("snacker"), params, random.Random(11))
    assert _types(a) == _types(b)
    for ea, eb in zip(a, b):
        assert ea == eb


def test_different_seeds_produce_different_outcomes_in_aggregate():
    params = _params()
    types_a = _types(respond(_viewer_state("snacker"), _impression("snacker"), params, random.Random(0)))
    seen = {tuple(types_a)}
    for s in range(1, 50):
        seen.add(tuple(_types(respond(_viewer_state("snacker"), _impression("snacker"), params, random.Random(s)))))
    assert len(seen) > 1  # not collapsed to a single deterministic outcome


# -----------------------------------------------------------------------------
# 4. Substrate-writer structural validation
# -----------------------------------------------------------------------------

def test_emitted_events_pass_substrate_writer_validation():
    params = _params()
    seen_types: set[str] = set()
    for i in range(500):
        evs = respond(_viewer_state("deep_watcher"), _impression("deep_watcher"), params, random.Random(i))
        for ev in evs:
            prepared = _prepare(ev)
            assert prepared["event_type"] == ev["event_type"]
            seen_types.add(ev["event_type"])
    # At minimum we should have exercised one of {watch, view_end} and at
    # least one chained action across 500 deep-watcher iterations.
    assert seen_types & {"watch", "view_end"}
    assert seen_types & {"like", "send", "save", "comment", "replay"}


def test_intra_day_seq_unique_per_response():
    params = _params()
    for i in range(100):
        evs = respond(_viewer_state("deep_watcher"), _impression("deep_watcher"), params, random.Random(i))
        seqs = [e["intra_day_seq"] for e in evs]
        assert len(set(seqs)) == len(seqs), f"duplicate seqs at seed {i}: {seqs}"


def test_skip_response_emits_only_a_single_skip():
    """When the skip path triggers, no chained actions tag along."""
    params = _params()
    # Very high p_skip so the skip path triggers reliably.
    forced = {**params, "segment_propensities": {
        "force_skip": {**params["segment_propensities"]["snacker"], "p_skip": 1.0}
    }}
    for i in range(20):
        evs = respond({"segment": "force_skip"}, _impression("force_skip"), forced, random.Random(i))
        assert _types(evs) == ["skip"]


def test_unknown_segment_uses_fallback_propensities():
    """An unknown segment should not raise; fallback propensities apply."""
    params = _params()
    counts = Counter()
    for i in range(200):
        evs = respond({"segment": "alien"}, _impression("alien"), params, random.Random(i))
        for e in evs:
            counts[e["event_type"]] += 1
    assert sum(counts.values()) > 0
