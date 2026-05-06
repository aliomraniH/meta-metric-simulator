"""Soft-mode integration test for Layer 8 (narrator).

The architectural canaries:

  1. test_narrator_no_invention_canary — clean output (only quotes
     input numbers) writes with disputed=False.
  2. test_narrator_invention_caught — output that mentions a number
     not in input writes BUT with disputed=True and an
     `invented_number` flag.

Together these two prove "narrator MUST NOT invent numbers" is
mechanically enforced (via the deterministic check inside
synthesize_insight), not just a prompt instruction.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import insights.tools.synthesize_insight as syn_module
from infra.db import insights as insights_table, metadata
from insights.deterministic import Anomaly
from insights.schemas import Hypothesis, Narrative


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_calibration(monkeypatch):
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module, "verify_lock_against_current_state", lambda **_: (True, [])
    )


@pytest.fixture
async def session_factory():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    yield factory
    await eng.dispose()


@pytest.fixture
def patched_persist(monkeypatch, session_factory):
    async def _persist(row):
        async with session_factory() as s:
            await s.execute(insights_table.insert(), [row])
            await s.commit()
    monkeypatch.setattr(syn_module, "_persist_row", _persist)
    return session_factory


def _anomaly() -> Anomaly:
    """The canonical canary anomaly: observed_value=42.7, z_score=3.5,
    tick_day=14, expected_value=20.0."""
    return Anomaly(
        scenario_id="scn-A",
        metric_name="ad_revenue_per_dau",
        tick_day=14,
        observed_value=42.7,
        expected_value=20.0,
        z_score=3.5,
        direction="above",
    )


def _anomaly_payload(a: Anomaly) -> dict:
    return {
        "ref":            f"{a.metric_name}:{a.tick_day}",
        "metric_name":    a.metric_name,
        "tick_day":       a.tick_day,
        "observed_value": a.observed_value,
        "expected_value": a.expected_value,
        "z_score":        a.z_score,
        "direction":      a.direction,
    }


def _narrative(text: str, *, ref: str = "ad_revenue_per_dau:14") -> Narrative:
    return Narrative(
        scenario_id="scn-A",
        metric_name="ad_revenue_per_dau",
        hypotheses=[Hypothesis(
            anomaly_ref=ref,
            hypothesis_text=text,
            confidence=0.7,
        )],
        overall_confidence=0.7,
    )


async def _row(factory):
    async with factory() as s:
        rows = (await s.execute(select(insights_table))).mappings().all()
    assert len(rows) == 1, f"expected exactly 1 insight row, got {len(rows)}"
    return rows[0]


# ---------------------------------------------------------------------------
# 1. CLEAN narrative — disputed=False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrator_no_invention_canary(session_factory, patched_persist):
    """Hypothesis only mentions numbers that are in the Anomaly input.
    Expected outcome: insight row written, disputed=False, no flags."""
    a = _anomaly()
    n = _narrative("The metric jumped to 42.7 on day 14, which is unusual.")
    result = await syn_module.synthesize_insight(
        narrative=n,
        anomalies_input=[_anomaly_payload(a)],
        ctx=AsyncMock(),
    )
    assert result["disputed"] is False
    assert result["narrator_flags"] == []
    row = await _row(session_factory)
    assert row["disputed"] == 0
    assert row["narrator_flags_json"] is None


# ---------------------------------------------------------------------------
# 2. INVENTED-NUMBER canary — disputed=True with invented_number flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrator_invention_caught(session_factory, patched_persist):
    """Hypothesis claims a 156% increase — 156 is NOT in the Anomaly's
    fields.  This is the load-bearing canary: the row is still written
    (soft mode) BUT disputed=True and `invented_number` flag fires.

    Without this enforcement, the narrator's "MUST NOT invent numbers"
    rule would be only a prompt instruction.  Test pinned so a future
    change that disables the deterministic check fails the build."""
    a = _anomaly()
    n = _narrative(
        "The metric jumped to 42.7 on day 14, suggesting 156% increase over baseline."
    )
    result = await syn_module.synthesize_insight(
        narrative=n,
        anomalies_input=[_anomaly_payload(a)],
        ctx=AsyncMock(),
    )
    flag_kinds = {f["kind"] for f in result["narrator_flags"]}
    assert "invented_number" in flag_kinds
    # Soft-mode contract: row IS still written.
    row = await _row(session_factory)
    assert row["disputed"] == 1
    assert result["disputed"] is True


# ---------------------------------------------------------------------------
# 3. End-to-end pipeline canary (deterministic + synthesizer)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrator_pipeline_end_to_end(session_factory, patched_persist):
    """Run the deterministic z_score_anomalies → narrator (mocked) →
    synthesize_insight pipeline end-to-end.  Verifies the data flow
    matches the prod path."""
    from insights.deterministic import z_score_anomalies

    class _RT:
        async def compute(self, metric_name, scenario_id, group_by=None, **_):
            series = [(i, 100.0 + (i % 3)) for i in range(14)] + [(14, 200.0)]
            return [{"dim": t, "value": v} for t, v in series]

    anomalies = await z_score_anomalies(
        scenario_id="scn-A",
        metric_name="ad_revenue_per_dau",
        metric_runtime=_RT(),
        window=10,
        threshold=2.5,
    )
    assert len(anomalies) == 1

    a = anomalies[0]
    payload = {
        "ref":            f"{a.metric_name}:{a.tick_day}",
        "metric_name":    a.metric_name,
        "tick_day":       a.tick_day,
        "observed_value": a.observed_value,
        "expected_value": a.expected_value,
        "z_score":        a.z_score,
        "direction":      a.direction,
    }
    n = Narrative(
        scenario_id="scn-A",
        metric_name="ad_revenue_per_dau",
        hypotheses=[Hypothesis(
            anomaly_ref=f"{a.metric_name}:{a.tick_day}",
            hypothesis_text=f"The metric reached {a.observed_value} on day {a.tick_day}, unusual.",
            confidence=0.8,
        )],
        overall_confidence=0.8,
    )
    result = await syn_module.synthesize_insight(
        narrative=n,
        anomalies_input=[payload],
        ctx=AsyncMock(),
    )
    assert result["narrator_flags"] == []
    row = await _row(session_factory)
    assert row["scenario_id"] == "scn-A"
    assert row["metric_name"] == "ad_revenue_per_dau"


# ---------------------------------------------------------------------------
# 4. Sole-writer canary — narrate doesn't write; synthesize does
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_only_synthesize_insight_writes(session_factory, patched_persist):
    from insights.tools.narrate_anomalies import narrate_anomalies

    class _RT:
        async def compute(self, metric_name, scenario_id, group_by=None, **_):
            return [{"dim": i, "value": 100.0} for i in range(20)]

    ctx = AsyncMock()
    # Narrate against stationary data → no anomalies → no model call,
    # but more importantly no insight row.
    n = await narrate_anomalies(
        scenario_id="scn-A", metric_name="m", ctx=ctx, metric_runtime=_RT(),
    )
    async with session_factory() as s:
        rows = (await s.execute(select(insights_table))).mappings().all()
    assert len(rows) == 0

    # Now the writer.
    payload = _anomaly_payload(_anomaly())
    n = _narrative("The metric was 42.7 on day 14, unusual.")
    await syn_module.synthesize_insight(
        narrative=n, anomalies_input=[payload], ctx=AsyncMock(),
    )
    async with session_factory() as s:
        rows = (await s.execute(select(insights_table))).mappings().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 5. Layer-isolation hook denies l8 writes outside scope
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_layer_isolation_hook_blocks_l8_writing_outside_insights():
    """The synthesize_insight tool hardcodes the insights table — it
    has no target_file parameter to redirect.  The SDK-boundary hook
    is belt-and-braces: it denies any synthesize_* tool whose payload
    points to a deterministic-layer prefix."""
    from orchestrator.hooks import make_layer_isolation_hook

    hook = make_layer_isolation_hook()
    result = await hook(
        "synthesize_insight",
        {"target_file": "params/segment_propensities.yaml"},
        {},
    )
    deny = result.get("hookSpecificOutput", {}).get("permissionDecision")
    assert deny == "deny"


# ---------------------------------------------------------------------------
# 6. Sampling channel intact through the l8 mount
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sampling_channel_intact_for_l8():
    from fastmcp import FastMCP

    from insights.server import insights_server
    import insights.tools.narrate_anomalies as na_module

    front_door = FastMCP("test-front-door-l8")
    front_door.mount(insights_server, namespace="l8")

    tools = await front_door.list_tools()
    by_name = {t.name for t in tools}
    expected = {"l8_find_similar_scenarios", "l8_narrate_anomalies", "l8_synthesize_insight"}
    assert expected <= by_name

    # Direct invocation of the inner module → ctx.sample reached.
    class _RT:
        async def compute(self, metric_name, scenario_id, group_by=None, **_):
            series = [(i, 100.0 + (i % 3)) for i in range(14)] + [(14, 200.0)]
            return [{"dim": t, "value": v} for t, v in series]

    spy = AsyncMock(return_value={
        "content": [{
            "type": "tool_use",
            "name": "emit_narrative",
            "input": {
                "scenario_id": "scn-A",
                "metric_name": "rev",
                "hypotheses": [{
                    "anomaly_ref": "rev:14",
                    "hypothesis_text": "The metric reached 200.0 on day 14, unusual.",
                    "confidence": 0.8,
                    "cited_supporting_scenarios": [],
                }],
                "overall_confidence": 0.8,
            },
        }],
    })
    ctx = AsyncMock()
    ctx.sample = spy

    result = await na_module.narrate_anomalies(
        scenario_id="scn-A",
        metric_name="rev",
        ctx=ctx,
        metric_runtime=_RT(),
    )
    assert isinstance(result, Narrative)
    assert spy.await_count == 1
    kwargs = spy.call_args.kwargs
    tools_arg = kwargs.get("tools") or []
    assert tools_arg and tools_arg[0]["name"] == "emit_narrative"
    assert tools_arg[0]["strict"] is True


# ---------------------------------------------------------------------------
# 7. Calibration drift blocks synthesize_insight before any model call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_calibration_drift_blocks_l8(monkeypatch, patched_persist):
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module,
        "verify_lock_against_current_state",
        lambda **_: (False, ["params/segment_propensities.yaml"]),
    )
    n = _narrative("The metric was 42.7 on day 14, unusual.")
    with pytest.raises(lock_module.CalibrationLockError):
        await syn_module.synthesize_insight(
            narrative=n,
            anomalies_input=[_anomaly_payload(_anomaly())],
            ctx=AsyncMock(),
        )


# ---------------------------------------------------------------------------
# 8. Front door surfaces all four real namespaces
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_front_door_surfaces_l4_l5_l6_l8():
    from fastmcp import FastMCP

    from baselines.server import baselines_server
    from curation.server import curation_server
    from engine.server import engine_server
    from insights.server import insights_server

    app = FastMCP("test-front-door")
    app.mount(baselines_server, namespace="l4")
    app.mount(curation_server, namespace="l5")
    app.mount(engine_server, namespace="l6")
    app.mount(insights_server, namespace="l8")

    tools = await app.list_tools()
    names = {t.name for t in tools}
    assert {
        "l4_synthesize_baseline", "l5_synthesize_diff",
        "l6_synthesize_scenario", "l8_synthesize_insight",
        "l8_find_similar_scenarios", "l8_narrate_anomalies",
    } <= names
