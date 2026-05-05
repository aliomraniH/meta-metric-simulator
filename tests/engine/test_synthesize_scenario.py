"""Tests for engine/tools/synthesize_scenario.py — Layer 6 sole writer.

Soft-mode contract (AGENTIC_ARCHITECTURE_INDEX.md §2.4):
  * NOT readOnlyHint (it writes)
  * No ctx.elicit (soft mode does not gate on user approval)
  * On verdict.passes → write with disputed=False
  * On N=2 exhaustion without score ≥ threshold → write with disputed=True
  * Calibration drift → CalibrationLockError BEFORE any model call
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import engine.tools.synthesize_scenario as syn_module
from engine.schemas_agentic import (
    AudienceFilter,
    CompiledScenario,
    EvaluatorVerdict,
    Perturbation,
)
from infra.db import metadata, scenarios as scenarios_table


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_calibration(monkeypatch):
    """Default: lock looks valid + non-drifting."""
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module, "verify_lock_against_current_state", lambda **_: (True, [])
    )
    yield


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
    """Wire `_persist_row` to the in-memory SQLite session_factory so tests
    can SELECT back rows after synthesis."""
    captured: list[dict] = []

    async def _persist(row):
        captured.append(row)
        async with session_factory() as s:
            await s.execute(scenarios_table.insert(), [row])
            await s.commit()

    monkeypatch.setattr(syn_module, "_persist_row", _persist)
    return {"captured": captured, "session_factory": session_factory}


@pytest.fixture
def patched_loop(monkeypatch):
    """Patch the critique-revise loop to a controllable AsyncMock."""
    spy = AsyncMock()
    monkeypatch.setattr(syn_module, "critique_revise_loop", spy)
    return spy


def _scenario(**overrides) -> CompiledScenario:
    base = dict(
        name="iter_scenario",
        description="test",
        natural_language_intent="what if foo?",
        perturbations=[Perturbation(
            target="ad_load_pct", op="set", value=0.15,
            rationale="test rationale",
        )],
        time_horizon="7d",
        audience_filters=[AudienceFilter(
            dim="viewer_segment", op="eq", value="snackers",
        )],
        confidence=0.85,
    )
    base.update(overrides)
    return CompiledScenario(**base)


def _verdict(score: float, *, threshold: float = 0.7) -> EvaluatorVerdict:
    return EvaluatorVerdict(
        score=score,
        rubric_breakdown={"schema_validity": score, "perturbation_realism": score,
                          "simulator_constrainability": score,
                          "audience_filter_coherence": score},
        gaps=[] if score >= 0.95 else ["needs work"],
        suggested_fixes=[] if score >= 0.95 else ["fix x"],
        accept_threshold=threshold,
    )


# ---------------------------------------------------------------------------
# 1. Pass on iter 1 → write with disputed=False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_writes_on_pass(patched_persist, patched_loop):
    patched_loop.return_value = (_scenario(), _verdict(0.92), 1)

    ctx = AsyncMock()
    result = await syn_module.synthesize_scenario(
        natural_language_intent="x", ctx=ctx,
    )
    assert result["disputed"] is False
    assert result["iterations_used"] == 1

    # Row was actually written.
    factory = patched_persist["session_factory"]
    async with factory() as s:
        rows = (await s.execute(select(scenarios_table))).mappings().all()
    assert len(rows) == 1
    assert rows[0]["disputed"] == 0
    assert rows[0]["scenario_id"] == result["scenario_id"]


# ---------------------------------------------------------------------------
# 2. Fail after N=2 → write with disputed=True (soft-mode contract)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_writes_disputed_on_n2_exhaustion(
    patched_persist, patched_loop
):
    patched_loop.return_value = (_scenario(), _verdict(0.55), 2)

    ctx = AsyncMock()
    result = await syn_module.synthesize_scenario(
        natural_language_intent="x", ctx=ctx,
    )
    assert result["disputed"] is True
    assert result["iterations_used"] == 2

    factory = patched_persist["session_factory"]
    async with factory() as s:
        rows = (await s.execute(select(scenarios_table))).mappings().all()
    assert len(rows) == 1
    assert rows[0]["disputed"] == 1


# ---------------------------------------------------------------------------
# 3. Evaluator verdict serialised to JSON column
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_persists_evaluator_verdict_json(
    patched_persist, patched_loop
):
    verdict = _verdict(0.55)
    patched_loop.return_value = (_scenario(), verdict, 2)

    ctx = AsyncMock()
    await syn_module.synthesize_scenario(natural_language_intent="x", ctx=ctx)

    factory = patched_persist["session_factory"]
    async with factory() as s:
        rows = (await s.execute(select(scenarios_table))).mappings().all()
    raw = rows[0]["evaluator_verdict_json"]
    if isinstance(raw, str):
        import json as _json
        raw = _json.loads(raw)
    assert raw["score"] == pytest.approx(0.55)
    assert raw["accept_threshold"] == pytest.approx(0.7)
    assert raw["gaps"] == ["needs work"]


# ---------------------------------------------------------------------------
# 4. ULID scenario_id returned
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_returns_scenario_id(patched_persist, patched_loop):
    patched_loop.return_value = (_scenario(), _verdict(0.95), 1)

    ctx = AsyncMock()
    result = await syn_module.synthesize_scenario(natural_language_intent="x", ctx=ctx)
    sid = result["scenario_id"]
    assert isinstance(sid, str) and len(sid) == 26  # ULID is 26 chars in canonical form


# ---------------------------------------------------------------------------
# 5. Calibration drift aborts BEFORE the loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_calibration_drift_aborts(
    patched_persist, patched_loop, monkeypatch
):
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module,
        "verify_lock_against_current_state",
        lambda **_: (False, ["params/segment_propensities.yaml"]),
    )

    ctx = AsyncMock()
    with pytest.raises(lock_module.CalibrationLockError):
        await syn_module.synthesize_scenario(natural_language_intent="x", ctx=ctx)
    # The loop NEVER ran.
    assert patched_loop.await_count == 0
    # No row written.
    factory = patched_persist["session_factory"]
    async with factory() as s:
        rows = (await s.execute(select(scenarios_table))).mappings().all()
    assert rows == []


# ---------------------------------------------------------------------------
# 6. Soft-mode: NEVER calls ctx.elicit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_does_not_elicit(patched_persist, patched_loop):
    patched_loop.return_value = (_scenario(), _verdict(0.92), 1)

    ctx = AsyncMock()
    ctx.elicit = AsyncMock(return_value=False)  # would block writes if called
    await syn_module.synthesize_scenario(natural_language_intent="x", ctx=ctx)
    # Soft-mode contract: no human gate on accept.  The disputed bit
    # is the user-visible signal, not a per-write approval prompt.
    assert ctx.elicit.await_count == 0


# ---------------------------------------------------------------------------
# 7. Tool registration — NOT readOnlyHint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_registered_not_readonly():
    from engine.server import engine_server
    tools = await engine_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert "synthesize_scenario" in by_name
    expected = {
        "get_audience_definition", "lookup_seasonality",
        "compile_scenario", "evaluate_scenario", "synthesize_scenario",
    }
    assert expected <= set(by_name)
    annotations = by_name["synthesize_scenario"].annotations
    assert getattr(annotations, "readOnlyHint", None) is not True


# ---------------------------------------------------------------------------
# 8. iterations_used column reflects loop output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_iterations_used_recorded(patched_persist, patched_loop):
    patched_loop.return_value = (_scenario(), _verdict(0.92), 2)

    ctx = AsyncMock()
    await syn_module.synthesize_scenario(natural_language_intent="x", ctx=ctx)

    factory = patched_persist["session_factory"]
    async with factory() as s:
        rows = (await s.execute(select(scenarios_table))).mappings().all()
    assert rows[0]["iterations_used"] == 2


# ---------------------------------------------------------------------------
# 9. _to_engine_perturbation translates ops → engine types
# ---------------------------------------------------------------------------

def test_op_to_engine_type_set():
    p = Perturbation(target="ad_load_pct", op="set", value=0.15, rationale="x")
    out = syn_module._to_engine_perturbation(p, horizon_days=7)
    assert out["type"] == "step"
    assert out["target"] == "ad_load_pct"
    assert out["value"] == pytest.approx(0.15)


def test_op_to_engine_type_ramp():
    p = Perturbation(target="ad_load_pct", op="ramp", value=0.15, rationale="x")
    out = syn_module._to_engine_perturbation(p, horizon_days=7)
    assert out["type"] == "ramp"
    assert out["from"] == 0.0
    assert out["to"] == pytest.approx(0.15)
    assert out["days"] == 7


def test_op_to_engine_type_spike():
    p = Perturbation(target="integrity_prevalence", op="spike", value=5.0, rationale="x")
    out = syn_module._to_engine_perturbation(p, horizon_days=28)
    assert out["type"] == "spike"
    assert out["to"] == pytest.approx(5.0)
    assert out["duration_days"] >= 1
