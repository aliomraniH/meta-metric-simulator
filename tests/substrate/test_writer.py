"""Substrate round-trip tests.

Acceptance from M2: write 10k events, read back, assert count and field
integrity.  Round-trip must complete in < 2s on Replit.

These tests run against an in-memory aiosqlite engine so they don't need
a real Postgres provisioned.  Production deployments use Postgres (see
infra/db.py); the schema is dialect-portable thanks to the JSONType
variant defined there.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from infra.db import events as events_table
from infra.db import metadata, scenarios as scenarios_table
from substrate.reader import EventReader
from substrate.writer import EventValidationError, EventWriter


# -----------------------------------------------------------------------------
# Per-test in-memory engine — sqlite+aiosqlite, isolated.  Module fixture
# would share state between tests; we want a clean slate each time.
# -----------------------------------------------------------------------------

@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


# -----------------------------------------------------------------------------
# Generators
# -----------------------------------------------------------------------------

def _make_scenario_row(scenario_id: str = "scn-test", horizon: int = 7) -> dict:
    return {
        "scenario_id": scenario_id,
        "name": "test scenario",
        "description": "M2 round-trip test",
        "perturbations": [],
        "horizon_days": horizon,
        "seed": 42,
        "disputed": 0,
        "confidence": None,
        "created_at": time.time(),
    }


def _make_events(scenario_id: str, n: int) -> list[dict]:
    """Generate a deterministic mix of event types totalling exactly n rows."""
    rows: list[dict] = []
    segments = ["teen", "young_adult", "snacker", "lean_back"]
    types_cycle = [
        "impression", "watch", "skip", "like", "send",
        "save", "comment", "view_end", "ad_impression",
    ]
    seq_per_day: dict[int, int] = {}
    for i in range(n):
        day = i // 1000  # 1k events per day
        seq_per_day[day] = seq_per_day.get(day, 0) + 1
        et = types_cycle[i % len(types_cycle)]
        viewer_id = f"v-{i % 200}"
        seg = segments[i % len(segments)]
        reel_id = f"r-{i % 500}"
        creator_id = f"c-{i % 50}"

        row: dict = {
            "scenario_id": scenario_id,
            "tick_day": day,
            "intra_day_seq": seq_per_day[day],
            "timestamp": float(i),
            "event_type": et,
            "viewer_id": viewer_id,
            "viewer_segment": seg,
            "reel_id": reel_id,
            "creator_id": creator_id,
        }
        if et == "impression":
            row.update(
                surface="reels_tab",
                pool="connected" if (i % 3) else "unconnected",
                rank_score=0.5 + (i % 100) / 200.0,
            )
        elif et in {"watch", "skip", "view_end"}:
            row["watch_duration_sec"] = 1.0 + (i % 30)
        elif et == "ad_impression":
            row.update(ad_impression=1, ad_revenue_usd=0.01 * (i % 10 + 1))
        rows.append(row)
    return rows


# -----------------------------------------------------------------------------
# Round-trip — the headline acceptance test
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_round_trip_10k_events(session_factory):
    scenario_id = "scn-10k"
    rows = _make_events(scenario_id, 10_000)

    t0 = time.perf_counter()
    async with session_factory() as s:
        await s.execute(scenarios_table.insert(), [_make_scenario_row(scenario_id)])
        writer = EventWriter(s, batch_size=2000)
        n = await writer.write_batch(rows)
        await s.commit()
        assert n == 10_000
    write_elapsed = time.perf_counter() - t0

    async with session_factory() as s:
        reader = EventReader(s)
        count = await reader.count(scenario_id)
    assert count == 10_000

    total_elapsed = time.perf_counter() - t0
    # Acceptance budget: < 2s on Replit.  We give the local sandbox the same
    # budget; if this drifts it's a regression worth investigating.
    assert total_elapsed < 2.0, (
        f"round trip took {total_elapsed:.3f}s "
        f"(write {write_elapsed:.3f}s)"
    )


# -----------------------------------------------------------------------------
# Field integrity
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_field_integrity_preserved_on_round_trip(session_factory):
    scenario_id = "scn-fields"
    async with session_factory() as s:
        await s.execute(scenarios_table.insert(), [_make_scenario_row(scenario_id)])
        writer = EventWriter(s)
        await writer.write({
            "scenario_id": scenario_id,
            "tick_day": 0,
            "intra_day_seq": 1,
            "timestamp": 0.0,
            "event_type": "impression",
            "viewer_id": "v-1",
            "viewer_segment": "teen",
            "viewer_geo": "US",
            "viewer_tenure_days": 42,
            "reel_id": "r-1",
            "creator_id": "c-1",
            "creator_tier": "head",
            "reel_duration_sec": 18.0,
            "reel_topic_cluster": "music",
            "surface": "reels_tab",
            "pool": "unconnected",
            "rank_score": 0.873,
            "payload": {"experiment_arm": "control"},
        })
        await s.commit()

    async with session_factory() as s:
        reader = EventReader(s)
        rows = await reader.fetch_ordered(scenario_id)
    assert len(rows) == 1
    r = rows[0]
    assert r["viewer_segment"] == "teen"
    assert r["viewer_geo"] == "US"
    assert r["viewer_tenure_days"] == 42
    assert r["creator_tier"] == "head"
    assert r["reel_topic_cluster"] == "music"
    assert r["pool"] == "unconnected"
    assert abs(r["rank_score"] - 0.873) < 1e-9
    # JSON payload survives the round trip whether stored as JSON or JSONB.
    payload = r["payload"]
    if isinstance(payload, str):
        import json as _json
        payload = _json.loads(payload)
    assert payload == {"experiment_arm": "control"}


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_event_type_rejected(session_factory):
    scenario_id = "scn-bad-type"
    async with session_factory() as s:
        await s.execute(scenarios_table.insert(), [_make_scenario_row(scenario_id)])
        writer = EventWriter(s)
        with pytest.raises(EventValidationError, match="unknown event_type"):
            await writer.write({
                "scenario_id": scenario_id,
                "tick_day": 0,
                "intra_day_seq": 1,
                "timestamp": 0.0,
                "event_type": "screech",  # not in the enum
                "viewer_id": "v-1",
                "reel_id": "r-1",
            })


@pytest.mark.asyncio
async def test_missing_required_field_rejected(session_factory):
    scenario_id = "scn-missing"
    async with session_factory() as s:
        await s.execute(scenarios_table.insert(), [_make_scenario_row(scenario_id)])
        writer = EventWriter(s)
        # 'impression' requires viewer_segment among others.
        with pytest.raises(EventValidationError, match="viewer_segment"):
            await writer.write({
                "scenario_id": scenario_id,
                "tick_day": 0,
                "intra_day_seq": 1,
                "timestamp": 0.0,
                "event_type": "impression",
                "viewer_id": "v-1",
                "reel_id": "r-1",
                "creator_id": "c-1",
                "surface": "reels_tab",
                "pool": "connected",
                "rank_score": 0.5,
                # viewer_segment missing
            })


@pytest.mark.asyncio
async def test_event_id_auto_assigned_as_ulid(session_factory):
    scenario_id = "scn-ulid"
    async with session_factory() as s:
        await s.execute(scenarios_table.insert(), [_make_scenario_row(scenario_id)])
        writer = EventWriter(s)
        eid = await writer.write({
            "scenario_id": scenario_id,
            "tick_day": 0,
            "intra_day_seq": 1,
            "timestamp": 0.0,
            "event_type": "like",
            "viewer_id": "v-1",
            "reel_id": "r-1",
        })
        await s.commit()
    assert isinstance(eid, str)
    assert len(eid) == 26


# -----------------------------------------------------------------------------
# Reader query path
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reader_query_groups_by_segment(session_factory):
    scenario_id = "scn-q"
    rows = _make_events(scenario_id, 4_000)
    async with session_factory() as s:
        await s.execute(scenarios_table.insert(), [_make_scenario_row(scenario_id)])
        writer = EventWriter(s, batch_size=1000)
        await writer.write_batch(rows)
        await s.commit()

    async with session_factory() as s:
        reader = EventReader(s)
        result = await reader.query(
            scenario_id,
            where={"event_type": "impression"},
            group_by=["viewer_segment"],
            agg="count",
        )
    # 4 segments, deterministic generator → all 4 present.
    segments = {r["viewer_segment"] for r in result}
    assert segments == {"teen", "young_adult", "snacker", "lean_back"}
    assert sum(int(r["value"]) for r in result) > 0


@pytest.mark.asyncio
async def test_reader_raw_sql_requires_scenario_id_param(session_factory):
    async with session_factory() as s:
        reader = EventReader(s)
        with pytest.raises(ValueError, match="scenario_id"):
            await reader.raw_sql("scn-x", "SELECT COUNT(*) FROM events")


@pytest.mark.asyncio
async def test_reader_rejects_disallowed_group_by(session_factory):
    async with session_factory() as s:
        reader = EventReader(s)
        with pytest.raises(ValueError, match="group_by"):
            await reader.query("scn-x", group_by=["payload"], agg="count")
