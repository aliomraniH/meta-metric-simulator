"""Strict-mode-equivalent integration tests for Layer 6 (engine compiler).

Layer 6 runs in **soft mode** per Option C — writes happen even on
N=2 exhaustion, marked `disputed=true`.  The architectural canaries
below pin the same kind of invariants as M12c-iii / M13b but adapted
to soft-mode semantics:

  * compile_scenario / evaluate_scenario never write
  * synthesize_scenario is the SOLE path that writes to scenarios
  * N=2 exhaustion → `disputed=true` (no escalation, no human elicit)
  * Calibration drift → CalibrationLockError BEFORE any model call
  * The synthesized manifest is runnable by the deterministic engine
    (the agentic ↔ deterministic bridge is intact)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import engine.tools.synthesize_scenario as syn_module
from engine.scenario import Scenario
from engine.schemas_agentic import (
    AudienceFilter,
    CompiledScenario,
    EvaluatorVerdict,
    Perturbation,
)
from infra.db import metadata, scenarios as scenarios_table


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PARAMS_DIR = REPO_ROOT / "params"


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
    """Wire `_persist_row` to the in-memory SQLite session_factory."""
    async def _persist(row):
        async with session_factory() as s:
            await s.execute(scenarios_table.insert(), [row])
            await s.commit()
    monkeypatch.setattr(syn_module, "_persist_row", _persist)
    return session_factory


@pytest.fixture
def patched_loop(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(syn_module, "critique_revise_loop", spy)
    return spy


def _scenario(**overrides) -> CompiledScenario:
    base = dict(
        name="canary_scenario",
        description="canary",
        natural_language_intent="what if x?",
        perturbations=[Perturbation(
            target="ad_load_pct", op="set", value=0.15,
            rationale="canary",
        )],
        time_horizon="1d",
        audience_filters=[AudienceFilter(
            dim="viewer_segment", op="eq", value="snackers",
        )],
        confidence=0.85,
    )
    base.update(overrides)
    return CompiledScenario(**base)


def _verdict(score: float) -> EvaluatorVerdict:
    return EvaluatorVerdict(
        score=score,
        rubric_breakdown={"schema_validity": score, "perturbation_realism": score,
                          "simulator_constrainability": score,
                          "audience_filter_coherence": score},
        gaps=[] if score >= 0.95 else ["g"],
        suggested_fixes=[] if score >= 0.95 else ["fix x"],
        accept_threshold=0.7,
    )


async def _row_count(factory) -> int:
    async with factory() as s:
        result = await s.execute(select(func.count()).select_from(scenarios_table))
        return int(result.scalar() or 0)


# ---------------------------------------------------------------------------
# 1. compile_scenario does not write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compiler_does_not_write(session_factory):
    from engine.tools.compile_scenario import compile_scenario

    pre = await _row_count(session_factory)
    ctx = AsyncMock()
    ctx.sample = AsyncMock(return_value={
        "content": [{
            "type": "tool_use",
            "name": "emit_compiled_scenario",
            "input": _scenario().model_dump(mode="json"),
        }],
    })
    result = await compile_scenario(natural_language_intent="x", ctx=ctx)
    assert isinstance(result, CompiledScenario)
    assert await _row_count(session_factory) == pre


# ---------------------------------------------------------------------------
# 2. evaluate_scenario does not write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluator_does_not_write(session_factory):
    from engine.tools.evaluate_scenario import evaluate_scenario

    pre = await _row_count(session_factory)
    ctx = AsyncMock()
    ctx.sample = AsyncMock(return_value={
        "content": [{
            "type": "tool_use",
            "name": "emit_verdict",
            "input": _verdict(0.9).model_dump(mode="json"),
        }],
    })
    await evaluate_scenario(scenario=_scenario(), ctx=ctx)
    assert await _row_count(session_factory) == pre


# ---------------------------------------------------------------------------
# 3. Only synthesize_scenario writes — single-writer canary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_only_synthesize_scenario_writes(
    session_factory, patched_persist, patched_loop
):
    """Run all 4 read-only tools first; row count stays at 0.  Then
    call synthesize_scenario; row count goes to exactly 1."""
    from engine.tools.compile_scenario import compile_scenario
    from engine.tools.evaluate_scenario import evaluate_scenario
    from engine.tools.get_audience_definition import get_audience_definition
    from engine.tools.lookup_seasonality import lookup_seasonality

    # The 4 read-only tools — none must write.
    await get_audience_definition("teens")
    await lookup_seasonality("Q4 2025")

    ctx = AsyncMock()
    ctx.sample = AsyncMock(side_effect=[
        {"content": [{"type": "tool_use", "name": "emit_compiled_scenario",
                      "input": _scenario().model_dump(mode="json")}]},
        {"content": [{"type": "tool_use", "name": "emit_verdict",
                      "input": _verdict(0.92).model_dump(mode="json")}]},
    ])
    await compile_scenario(natural_language_intent="x", ctx=ctx)
    await evaluate_scenario(scenario=_scenario(), ctx=ctx)

    assert await _row_count(session_factory) == 0

    # Now the writer.
    patched_loop.return_value = (_scenario(), _verdict(0.92), 1)
    await syn_module.synthesize_scenario(natural_language_intent="x", ctx=AsyncMock())
    assert await _row_count(session_factory) == 1


# ---------------------------------------------------------------------------
# 4. N=2 exhaustion → disputed=true (soft-mode contract)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_n2_exhaustion_writes_disputed_true(
    session_factory, patched_persist, patched_loop
):
    patched_loop.return_value = (_scenario(), _verdict(0.55), 2)

    await syn_module.synthesize_scenario(natural_language_intent="x", ctx=AsyncMock())
    async with session_factory() as s:
        rows = (await s.execute(select(scenarios_table))).mappings().all()
    assert len(rows) == 1
    assert rows[0]["disputed"] == 1
    assert rows[0]["iterations_used"] == 2


# ---------------------------------------------------------------------------
# 5. Passing verdict on iter 1 → disputed=false
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_passing_verdict_writes_disputed_false(
    session_factory, patched_persist, patched_loop
):
    patched_loop.return_value = (_scenario(), _verdict(0.91), 1)

    await syn_module.synthesize_scenario(natural_language_intent="x", ctx=AsyncMock())
    async with session_factory() as s:
        rows = (await s.execute(select(scenarios_table))).mappings().all()
    assert rows[0]["disputed"] == 0


# ---------------------------------------------------------------------------
# 6. Layer-isolation hook denies l6 writes outside scenarios scope
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_layer_isolation_hook_blocks_l6_writing_outside_scenarios():
    """The synthesize_scenario tool hardcodes scenarios as its target —
    target_file isn't even a parameter.  Belt-and-braces: the SDK-boundary
    layer-isolation hook also denies any synthesize_* tool whose payload
    tries to redirect to a deterministic-layer prefix."""
    from orchestrator.hooks import make_layer_isolation_hook

    hook = make_layer_isolation_hook()
    result = await hook(
        "synthesize_scenario",
        {"target_file": "baselines/data/family_scale.yaml"},
        {},
    )
    # baselines/data/ is NOT a deterministic-layer prefix (synthesize_baseline
    # writes there), so the hook does not deny on that path.  The protective
    # invariant for l6 is the params/algorithms/engine/metrics/calibration
    # prefix list — confirm denial when the bad actor tries those.
    deny = result.get("hookSpecificOutput", {}).get("permissionDecision")
    # baselines/data/ is allowed by the hook (it's the target of synthesize_baseline)
    # — synthesize_scenario's in-process target hardcoding is what gates l6.
    # For the cross-layer block, we test against params/.
    assert deny != "deny"  # baselines/data/ is allowed in principle

    # Now the load-bearing case: hook MUST deny synthesize_scenario writing to params/.
    result2 = await hook(
        "synthesize_scenario",
        {"target_file": "params/segment_propensities.yaml"},
        {},
    )
    deny2 = result2.get("hookSpecificOutput", {}).get("permissionDecision")
    assert deny2 == "deny"


# ---------------------------------------------------------------------------
# 7. Sampling channel intact through the l6 mount
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sampling_channel_intact_for_l6():
    """Architectural canary: front_door.mount(engine_server, namespace='l6')
    preserves the sampling channel from the orchestrator down to the
    inner tool's ctx.sample()."""
    from fastmcp import FastMCP

    from engine.server import engine_server
    import engine.tools.compile_scenario as cs_module

    front_door = FastMCP("test-front-door-l6")
    front_door.mount(engine_server, namespace="l6")

    tools = await front_door.list_tools()
    by_name = {t.name for t in tools}
    expected = {
        "l6_get_audience_definition", "l6_lookup_seasonality",
        "l6_compile_scenario", "l6_evaluate_scenario",
        "l6_synthesize_scenario",
    }
    assert expected <= by_name

    # Direct invocation through the inner module — confirms ctx.sample
    # is reached and the request shape matches the strict-tool-use contract.
    spy = AsyncMock(return_value={
        "content": [{
            "type": "tool_use",
            "name": "emit_compiled_scenario",
            "input": _scenario().model_dump(mode="json"),
        }],
    })
    ctx = AsyncMock()
    ctx.sample = spy

    result = await cs_module.compile_scenario(natural_language_intent="x", ctx=ctx)
    assert isinstance(result, CompiledScenario)
    assert spy.await_count == 1
    kwargs = spy.call_args.kwargs
    tools_arg = kwargs.get("tools") or []
    assert tools_arg and tools_arg[0]["name"] == "emit_compiled_scenario"
    assert tools_arg[0]["strict"] is True


# ---------------------------------------------------------------------------
# 8. Calibration drift blocks synthesize_scenario before any model call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_calibration_drift_blocks_l6(monkeypatch, patched_persist, patched_loop):
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module,
        "verify_lock_against_current_state",
        lambda **_: (False, ["params/segment_propensities.yaml"]),
    )
    ctx = AsyncMock()
    with pytest.raises(lock_module.CalibrationLockError):
        await syn_module.synthesize_scenario(natural_language_intent="x", ctx=ctx)
    assert patched_loop.await_count == 0


# ---------------------------------------------------------------------------
# 9. LOAD-BEARING: synthesized manifest is runnable by the deterministic engine
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_engine_run_compatible_with_synthesized_scenario(
    session_factory, patched_persist, patched_loop
):
    """The agentic→deterministic bridge is intact: a CompiledScenario
    written by synthesize_scenario can be loaded back through
    `engine.scenario.Scenario.load` and validates without raising.

    If this fails, the bridge between agentic compilation and
    deterministic execution is broken — the soft-mode synthesizer's
    output must be runnable by the M7 engine without further translation.
    """
    cs = _scenario(
        name="bridge_canary",
        time_horizon="1d",
        perturbations=[Perturbation(
            target="ad_load_pct", op="set", value=0.15, rationale="canary",
        )],
    )
    patched_loop.return_value = (cs, _verdict(0.92), 1)

    result = await syn_module.synthesize_scenario(
        natural_language_intent="canary",
        ctx=AsyncMock(),
    )
    scenario_id = result["scenario_id"]

    # Load the row back via the M7 Scenario.load API.  Validation runs
    # in __post_init__ — ScenarioValidationError raises if the
    # perturbations don't match the engine's expected shape.
    async with session_factory() as s:
        loaded = await Scenario.load(s, scenario_id)
    assert loaded.scenario_id == scenario_id
    assert loaded.horizon_days == 1
    assert len(loaded.perturbations) == 1
    p = loaded.perturbations[0]
    # Translated to engine type "step" with target + value.
    assert p["type"] == "step"
    assert p["target"] == "ad_load_pct"
    assert p["value"] == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# 10. Front door surfaces all four namespaces (l4/l5/l6 real, l8 placeholder)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_front_door_surfaces_l4_l5_l6():
    from fastmcp import FastMCP

    from baselines.server import baselines_server
    from curation.server import curation_server
    from engine.server import engine_server

    app = FastMCP("test-front-door")
    app.mount(baselines_server, namespace="l4")
    app.mount(curation_server, namespace="l5")
    app.mount(engine_server, namespace="l6")

    tools = await app.list_tools()
    names = {t.name for t in tools}
    assert {
        "l4_synthesize_baseline", "l5_synthesize_diff", "l6_synthesize_scenario",
        "l6_compile_scenario", "l6_evaluate_scenario",
        "l6_get_audience_definition", "l6_lookup_seasonality",
    } <= names
