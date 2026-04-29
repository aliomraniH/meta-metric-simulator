"""Engine determinism + invariant tests.

Six tests per the M7 spec:
  1. Same seed twice → identical event sequences (compare ordering tuple).
  2. Different seeds → different sequences.
  3. Ramp perturbation hits expected value at midpoint tick.
  4. Empty perturbations → 14-day run completes without error.
  5. integrity_prevalence stays in [0.0, 1.0] across 14 days.
  6. 14-day scenario completes in <60s on Reserved-VM-equivalent hardware.

All tests use a small in-memory aiosqlite engine and very small
populations so the suite runs in a few seconds end-to-end.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from engine.scenario import Scenario
from engine.simulator import Simulator
from infra.db import metadata


PARAMS_DIR = str(Path(__file__).resolve().parent.parent.parent / "params")

# Tight populations — keep the suite fast.  Larger sizes are exercised
# in the production Simulator defaults; tests just need enough to
# generate non-trivial event sequences.
TEST_VIEWERS = 12
TEST_CREATORS = 6
TEST_REELS = 30


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
async def session_factory():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    yield factory
    await eng.dispose()


@pytest.fixture
def simulator(session_factory):
    return Simulator(
        session_factory,
        params_dir=PARAMS_DIR,
        viewers_n=TEST_VIEWERS,
        creators_n=TEST_CREATORS,
        reels_n=TEST_REELS,
    )


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

# Fingerprint columns — the substrate ordering tuple from M2 EVENT_SCHEMA.
# event_id is wall-clock derived (ULID) so excluded; ad_revenue_usd is
# included because monetization sampling is part of the determinism
# contract.
_FINGERPRINT_COLS = (
    "tick_day",
    "intra_day_seq",
    "event_type",
    "viewer_id",
    "viewer_segment",
    "reel_id",
    "creator_id",
    "watch_duration_sec",
    "ad_revenue_usd",
)


async def _fingerprint(session_factory, scenario_id: str) -> list[tuple]:
    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT tick_day, intra_day_seq, event_type, viewer_id, "
                "viewer_segment, reel_id, creator_id, watch_duration_sec, "
                "ad_revenue_usd "
                "FROM events WHERE scenario_id = :sid "
                "ORDER BY tick_day, intra_day_seq, event_type, viewer_id"
            ),
            {"sid": scenario_id},
        )
        return [tuple(row) for row in result]


async def _event_count(session_factory, scenario_id: str) -> int:
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT COUNT(*) FROM events WHERE scenario_id = :sid"),
            {"sid": scenario_id},
        )
        return int(result.scalar_one())


# -----------------------------------------------------------------------------
# 1. Same seed twice → identical event sequences
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_same_seed_produces_identical_event_sequences(simulator, session_factory):
    a = Scenario(name="det-a", horizon_days=3, seed=42)
    b = Scenario(name="det-b", horizon_days=3, seed=42)
    await simulator.run(a)
    await simulator.run(b)

    fp_a = await _fingerprint(session_factory, a.scenario_id)
    fp_b = await _fingerprint(session_factory, b.scenario_id)

    assert len(fp_a) > 0, "scenario should have produced events"
    assert fp_a == fp_b, (
        f"determinism violated: lens=({len(fp_a)},{len(fp_b)}); "
        f"first divergence at index "
        f"{next((i for i,(x,y) in enumerate(zip(fp_a, fp_b)) if x != y), 'len mismatch')}"
    )


# -----------------------------------------------------------------------------
# 2. Different seeds → different sequences
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_different_seeds_produce_different_sequences(simulator, session_factory):
    a = Scenario(name="seed-a", horizon_days=3, seed=42)
    b = Scenario(name="seed-b", horizon_days=3, seed=43)
    await simulator.run(a)
    await simulator.run(b)

    fp_a = await _fingerprint(session_factory, a.scenario_id)
    fp_b = await _fingerprint(session_factory, b.scenario_id)

    # The two sequences should differ somewhere — viewer pools are
    # sampled per-rng so even tiny seed diffs propagate.
    assert fp_a != fp_b


# -----------------------------------------------------------------------------
# 3. Ramp perturbation midpoint
# -----------------------------------------------------------------------------

def test_engine_perturbation_wrapper_ramp_midpoint():
    """Direct unit test of the engine's perturbation wrapper — it should
    produce the same midpoint value as the algorithm when given a ramp
    starting at start_day=0."""
    from engine.perturbations import apply_active_perturbations

    state = {"monetization": {"ad_load_policy": {"probability": 0.22}}}
    pert = [{
        "type": "ramp",
        "target": "monetization.ad_load_policy.probability",
        "from": 0.22,
        "to": 0.27,
        "days": 14,
    }]
    out = apply_active_perturbations(state, pert, tick_day=7)
    assert abs(out["monetization"]["ad_load_policy"]["probability"] - 0.245) < 1e-9


def test_engine_perturbation_wrapper_respects_start_day():
    """Perturbations whose start_day is in the future are skipped."""
    from engine.perturbations import apply_active_perturbations

    state = {"monetization": {"ad_load_policy": {"probability": 0.22}}}
    pert = [{
        "type": "step",
        "target": "monetization.ad_load_policy.probability",
        "value": 0.50,
        "start_day": 5,
    }]
    # Before start_day → unchanged.
    out_pre = apply_active_perturbations(state, pert, tick_day=2)
    assert out_pre["monetization"]["ad_load_policy"]["probability"] == 0.22
    # At start_day → applied.
    out_at = apply_active_perturbations(state, pert, tick_day=5)
    assert out_at["monetization"]["ad_load_policy"]["probability"] == 0.50


# -----------------------------------------------------------------------------
# 4. Empty perturbations, 14-day run completes
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_perturbations_run_completes_and_writes_events(simulator, session_factory):
    s = Scenario(name="bare-run", horizon_days=14, seed=1, perturbations=[])
    await simulator.run(s)
    n = await _event_count(session_factory, s.scenario_id)
    assert n > 0, "14-day scenario should produce events"


# -----------------------------------------------------------------------------
# 5. integrity_prevalence stays in [0.0, 1.0]
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integrity_prevalence_within_bounds_across_run(simulator, session_factory):
    """Run a 14-day scenario with a spike that pushes prevalence high,
    then verify the running prevalence stays in the [0,1] envelope."""
    s = Scenario(
        name="integrity-bounds",
        horizon_days=14,
        seed=7,
        perturbations=[
            {
                "type": "spike",
                "target": "violating_prevalence",
                "to": 0.50,
                "duration_days": 2,
                "start_day": 5,
            }
        ],
    )
    # Patch the simulator's run to surface the per-tick prevalence.
    # Easiest path: re-import the engine pieces and drive ticks directly.
    from engine.state import WorldState
    from engine.tick import run_tick

    async with session_factory() as session:
        await s.save(session)
        from substrate.writer import EventWriter
        writer = EventWriter(session)
        writer.scenario_id = s.scenario_id  # type: ignore[attr-defined]

        state = WorldState.bootstrap(
            seed=s.seed,
            params=simulator.params,
            viewers_n=TEST_VIEWERS,
            creators_n=TEST_CREATORS,
            reels_n=TEST_REELS,
        )
        for _ in range(s.horizon_days):
            state = await run_tick(state, s.perturbations, simulator.params, writer)
            p = float(state.integrity_prevalence["violating_prevalence"])
            assert 0.0 <= p <= 1.0, f"prevalence out of bounds at tick={state.tick_day}: {p}"
        await session.commit()


# -----------------------------------------------------------------------------
# 6. 14-day run timing budget
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fourteen_day_run_completes_within_60_seconds(simulator, session_factory):
    s = Scenario(name="perf-14d", horizon_days=14, seed=2)
    t0 = time.perf_counter()
    await simulator.run(s)
    elapsed = time.perf_counter() - t0
    # The Replit Reserved VM target is 60s for the production population
    # (~1000 viewers); with the test populations (~12 viewers) we use a
    # generous 30s ceiling — anything slower is a regression worth
    # investigating, almost certainly in the per-viewer candidate-pool
    # generation path.
    assert elapsed < 30.0, f"14-day run took {elapsed:.2f}s (test budget 30s)"


# -----------------------------------------------------------------------------
# Scenario validation tests (lightweight, sync)
# -----------------------------------------------------------------------------

def test_scenario_rejects_horizon_out_of_range():
    from engine.scenario import Scenario, ScenarioValidationError

    with pytest.raises(ScenarioValidationError):
        Scenario(name="x", horizon_days=0, seed=1)
    with pytest.raises(ScenarioValidationError):
        Scenario(name="x", horizon_days=400, seed=1)


def test_scenario_rejects_unknown_perturbation_type():
    from engine.scenario import Scenario, ScenarioValidationError

    with pytest.raises(ScenarioValidationError):
        Scenario(name="x", horizon_days=3, seed=1, perturbations=[
            {"type": "wiggle", "target": "monetization.ad_load_policy.probability", "value": 0.5}
        ])


def test_scenario_rejects_perturbation_missing_required_field():
    from engine.scenario import Scenario, ScenarioValidationError

    # ramp missing 'days'
    with pytest.raises(ScenarioValidationError):
        Scenario(name="x", horizon_days=3, seed=1, perturbations=[
            {"type": "ramp", "target": "x", "from": 0.1, "to": 0.2}
        ])


def test_scenario_save_load_roundtrip(session_factory):
    """Sync wrapper around the async save/load to catch shape errors."""
    import asyncio

    s = Scenario(
        name="rt",
        horizon_days=5,
        seed=99,
        perturbations=[{"type": "step", "target": "x.y", "value": 1.0}],
        description="round-trip",
    )

    async def _go():
        async with session_factory() as session:
            await s.save(session)
            loaded = await Scenario.load(session, s.scenario_id)
        return loaded

    loaded = asyncio.run(_go())
    assert loaded.name == "rt"
    assert loaded.horizon_days == 5
    assert loaded.seed == 99
    assert len(loaded.perturbations) == 1
    assert loaded.perturbations[0]["type"] == "step"
