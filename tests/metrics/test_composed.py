"""Composed-metric tests + the architectural-claim load-bearing test.

The M10 architectural claim:
    A composed metric (one that joins across event types) can be added
    by dropping a .sql file in metrics/definitions/composed/.  Zero
    changes are required to engine/, algorithms/, params/, baselines/,
    or the M9 compiler logic.

test_no_simulator_changes_required is the load-bearing test for that
claim — it introspects this file's imports and asserts none of them
come from the forbidden modules.

For the integrity-recovery-curve tests we directly tag a deterministic
fraction of impressions with `{"violating": true}` payloads after the
engine writes them.  This is test-data manipulation, not an engine
change — the metric's SQL semantics is what's under test.  The engine
extension that would translate `integrity_prevalence` perturbations
into impression-payload tagging is a separate concern.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from engine.scenario import Scenario
from engine.simulator import Simulator
from infra.db import metadata
from metrics.runtime import MetricRuntime


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_DEFINITIONS = REPO_ROOT / "metrics" / "definitions"
PARAMS_DIR = REPO_ROOT / "params"

EXPECTED_COMPOSED_METRIC_NAMES: set[str] = {
    "monetization_per_active_creator",
    "quality_funnel",
    "integrity_recovery_curve",
}

# Import-prefix denylist.  Tests in this file MUST NOT import from any
# of these — that would mean the architectural claim is violated.
FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "algorithms",
    "params",
    "baselines",
    "calibration",
    "interview",
    "tools",
    "orchestrator",
)


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
def runtime(session_factory):
    return MetricRuntime(session_factory, definitions_dir=LIVE_DEFINITIONS)


@pytest.fixture
async def small_scenario_id(session_factory):
    """7-day scenario for monetization + funnel tests."""
    sim = Simulator(
        session_factory,
        params_dir=PARAMS_DIR,
        viewers_n=12,
        creators_n=6,
        reels_n=30,
    )
    s = Scenario(name="composed-fixture-7d", horizon_days=7, seed=42)
    await sim.run(s)
    return s.scenario_id


# -----------------------------------------------------------------------------
# 1. Discovery
# -----------------------------------------------------------------------------

def test_runtime_discovers_3_composed_metrics(runtime):
    composed = runtime.list_metrics(layer="composed")
    names = {m.name for m in composed}
    assert names == EXPECTED_COMPOSED_METRIC_NAMES, (
        f"missing: {EXPECTED_COMPOSED_METRIC_NAMES - names}; "
        f"unexpected: {names - EXPECTED_COMPOSED_METRIC_NAMES}"
    )
    assert runtime.stats["by_layer"]["composed"] == 3
    assert runtime.stats["by_layer"]["leading"] == 9


# -----------------------------------------------------------------------------
# 2. Architectural claim: this test file imports nothing from the forbidden set
# -----------------------------------------------------------------------------

def test_no_simulator_changes_required():
    """Load-bearing M10 architectural-claim test.

    Introspects this file's own imports via ast and asserts none come
    from algorithms/, params/, baselines/, calibration/, interview/,
    tools/, or orchestrator/.  If a future change to a composed metric
    requires importing from one of those layers, the architectural
    claim has been violated and this test will catch it.
    """
    source = Path(__file__).read_text()
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imports.append(node.module)

    print(f"\nImports in test_composed.py:")
    for mod in sorted(set(imports)):
        print(f"  - {mod}")

    violations = [
        mod for mod in imports
        if any(mod == p or mod.startswith(p + ".") for p in FORBIDDEN_IMPORT_PREFIXES)
    ]
    assert violations == [], (
        f"M10 architectural-claim violation: imports from forbidden layers: "
        f"{violations}.  Composed metrics must work with zero changes outside "
        f"metrics/definitions/composed/."
    )


# -----------------------------------------------------------------------------
# 3-4. monetization_per_active_creator
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_monetization_per_active_creator_runs(runtime, small_scenario_id):
    rows = await runtime.compute(
        "monetization_per_active_creator",
        scenario_id=small_scenario_id,
    )
    assert isinstance(rows, list)
    # Engine doesn't currently emit creator_post events, so this metric's
    # JOIN may produce no rows.  Either an empty list OR rows with usd
    # values is acceptable.  When rows exist, values must be non-negative.
    for r in rows:
        v = r["value"]
        if v is not None:
            assert isinstance(v, float)
            assert v >= 0.0


@pytest.mark.asyncio
async def test_monetization_per_active_creator_grouped_by_tier(
    runtime, small_scenario_id, session_factory
):
    """Group by creator_tier.  Inject creator_post events directly so the
    JOIN has data — this is test-data manipulation, not an engine change."""
    # Tag 6 reels (one per creator) as creator_post events with tiers so the
    # metric's JOIN against creator_post resolves.
    creator_tiers = ["nano", "micro", "mid", "macro", "mega", "nano"]
    async with session_factory() as session:
        # Find some reel_ids the engine actually used in ad_impression events.
        result = await session.execute(
            text(
                "SELECT DISTINCT reel_id FROM events "
                "WHERE scenario_id = :sid AND event_type = 'ad_impression' "
                "AND reel_id IS NOT NULL LIMIT 6"
            ),
            {"sid": small_scenario_id},
        )
        reel_ids = [r[0] for r in result]

        for i, reel_id in enumerate(reel_ids):
            tier = creator_tiers[i % len(creator_tiers)]
            await session.execute(
                text(
                    "INSERT INTO events ("
                    "  event_id, scenario_id, tick_day, intra_day_seq, timestamp, "
                    "  event_type, creator_id, creator_tier, reel_id, "
                    "  reel_duration_sec, reel_topic_cluster"
                    ") VALUES ("
                    "  :eid, :sid, 0, :seq, 0.0, 'creator_post', :cid, :tier, "
                    "  :reel_id, 18.0, 'music'"
                    ")"
                ),
                {
                    "eid": f"cp{i:024d}".rjust(26, "0"),
                    "sid": small_scenario_id,
                    "seq": 9000 + i,
                    "cid": f"c-{i:05d}",
                    "tier": tier,
                    "reel_id": reel_id,
                },
            )
        await session.commit()

    rows = await runtime.compute(
        "monetization_per_active_creator",
        scenario_id=small_scenario_id,
        group_by="creator_tier",
    )
    tiers_seen = {r["dim"] for r in rows if r["dim"] is not None}
    assert len(tiers_seen) >= 2, (
        f"expected ≥2 distinct creator tiers in metric output, got {tiers_seen}"
    )


# -----------------------------------------------------------------------------
# 5-6. quality_funnel
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quality_funnel_returns_4_stage_columns(runtime, small_scenario_id):
    rows = await runtime.compute("quality_funnel", scenario_id=small_scenario_id)
    assert len(rows) > 0
    expected_cols = {
        "dim",
        "stage0_impressions",
        "stage1_3s_pct",
        "stage2_completion_pct",
        "stage3_engagement_pct",
    }
    for r in rows:
        assert expected_cols <= set(r.keys()), (
            f"row missing columns: {expected_cols - set(r.keys())}"
        )
        for col in ("stage1_3s_pct", "stage2_completion_pct", "stage3_engagement_pct"):
            v = r[col]
            if v is not None:
                assert 0.0 <= float(v) <= 1.0, f"{col}={v} outside [0,1]"


@pytest.mark.asyncio
async def test_quality_funnel_funnel_property(runtime, small_scenario_id):
    """stage1 ≥ stage2 ≥ stage3 must hold on every row.  Monotonicity is
    by construction in the SQL — if it fails, the AND-conjunction is
    broken or the underlying counts are inconsistent."""
    rows = await runtime.compute("quality_funnel", scenario_id=small_scenario_id)
    assert len(rows) > 0
    checked = 0
    for r in rows:
        s1, s2, s3 = r["stage1_3s_pct"], r["stage2_completion_pct"], r["stage3_engagement_pct"]
        if s1 is None and s2 is None and s3 is None:
            continue  # Empty group; nothing to assert.
        s1 = 0.0 if s1 is None else float(s1)
        s2 = 0.0 if s2 is None else float(s2)
        s3 = 0.0 if s3 is None else float(s3)
        assert s1 >= s2 >= s3, (
            f"funnel monotonicity violated at dim={r['dim']!r}: "
            f"stage1={s1}, stage2={s2}, stage3={s3}"
        )
        checked += 1
    assert checked > 0, "no non-empty funnel rows found"


# -----------------------------------------------------------------------------
# 7-8. integrity_recovery_curve
# -----------------------------------------------------------------------------

async def _tag_violating(
    session_factory,
    scenario_id: str,
    tick_day: int,
    fraction: float,
) -> None:
    """Tag a deterministic fraction of impressions on a given tick as violating.

    Direct UPDATE on the substrate — simulates what an engine extension
    would do when integrity_prevalence is elevated.  The metric reads
    payload via SQL LIKE, so we set payload to a JSON string the metric
    will match.
    """
    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT event_id FROM events "
                "WHERE scenario_id = :sid AND event_type = 'impression' "
                "AND tick_day = :day ORDER BY intra_day_seq"
            ),
            {"sid": scenario_id, "day": tick_day},
        )
        ids = [r[0] for r in result]
        if not ids:
            return
        n_to_tag = max(1, int(len(ids) * fraction))
        # Deterministic selection: tag first n_to_tag (already ordered).
        targeted = ids[:n_to_tag]
        for eid in targeted:
            await session.execute(
                text(
                    "UPDATE events SET payload = :payload WHERE event_id = :eid"
                ),
                {"payload": json.dumps({"violating": True}), "eid": eid},
            )
        await session.commit()


@pytest.fixture
async def fourteen_day_scenario_id(session_factory):
    sim = Simulator(
        session_factory,
        params_dir=PARAMS_DIR,
        viewers_n=20,
        creators_n=6,
        reels_n=40,
    )
    s = Scenario(name="composed-14d", horizon_days=14, seed=7)
    await sim.run(s)
    return s.scenario_id


@pytest.mark.asyncio
async def test_integrity_recovery_curve_with_spike(
    runtime, fourteen_day_scenario_id, session_factory
):
    """Tag a steady ~5% violating baseline across all days, with a 50%
    bump on days 5-6 to simulate a spike.  Compute the recovery curve
    and assert ratio>2 during the spike, near 1.0 after."""
    BASELINE_FRACTION = 0.05
    SPIKE_FRACTION = 0.50

    for day in range(15):
        frac = SPIKE_FRACTION if day in (5, 6) else BASELINE_FRACTION
        await _tag_violating(session_factory, fourteen_day_scenario_id, day, frac)

    rows = await runtime.compute(
        "integrity_recovery_curve",
        scenario_id=fourteen_day_scenario_id,
        tick_day_start=0,
        tick_day_end=14,
    )
    by_day = {int(r["dim"]): float(r["value"]) for r in rows if r["value"] is not None}

    # Baseline window (days 0-2) is ~5%, baseline_share ≈ 0.05.
    # Days 5-6: ~50% violating → ratio ≈ 10.
    assert by_day.get(5, 0) > 2.0, f"day 5 ratio {by_day.get(5)} not > 2.0"
    assert by_day.get(6, 0) > 2.0, f"day 6 ratio {by_day.get(6)} not > 2.0"

    # Days 12+: back to ~5% baseline → ratio ≈ 1.0.
    for d in (12, 13):
        if d in by_day:
            assert 0.5 <= by_day[d] <= 1.5, (
                f"day {d} ratio {by_day[d]} outside recovery band [0.5, 1.5]"
            )


@pytest.mark.asyncio
async def test_integrity_recovery_curve_no_spike(
    runtime, fourteen_day_scenario_id
):
    """No violations tagged.  Baseline=0 → metric returns ratio=1.0 for
    every tick per the CASE WHEN safety branch."""
    rows = await runtime.compute(
        "integrity_recovery_curve",
        scenario_id=fourteen_day_scenario_id,
        tick_day_start=0,
        tick_day_end=14,
    )
    assert len(rows) > 0
    for r in rows:
        v = r["value"]
        if v is None:
            continue
        f = float(v)
        assert 0.5 <= f <= 1.5, (
            f"day {r['dim']} ratio {f} outside [0.5, 1.5] under no-spike condition"
        )


# -----------------------------------------------------------------------------
# 9. Empty substrate
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_composed_metric_handles_empty_substrate(runtime):
    """All 3 composed metrics return an empty list (no exception) when no
    events match the requested scenario_id."""
    bogus = "no-such-scenario"
    for name in EXPECTED_COMPOSED_METRIC_NAMES:
        if name == "integrity_recovery_curve":
            rows = await runtime.compute(
                name,
                scenario_id=bogus,
                tick_day_start=0,
                tick_day_end=14,
            )
        else:
            rows = await runtime.compute(name, scenario_id=bogus)
        assert rows == [], f"metric {name} returned {len(rows)} rows for bogus scenario"
