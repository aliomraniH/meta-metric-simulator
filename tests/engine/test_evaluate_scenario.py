"""Tests for engine/tools/evaluate_scenario.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from engine.schemas_agentic import (
    AudienceFilter,
    CompiledScenario,
    EvaluatorVerdict,
    Perturbation,
)
from engine.tools.evaluate_scenario import evaluate_scenario


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scenario(**overrides) -> CompiledScenario:
    base = dict(
        name="teen_reels_only_90d",
        description="Reels-only for teens, 90d.",
        natural_language_intent="what if Reels-only mode launched for teens for 90 days?",
        perturbations=[Perturbation(
            target="reels_share_of_time", op="set", value=1.0,
            rationale="Reels-only = 100%.",
        )],
        time_horizon="90d",
        audience_filters=[AudienceFilter(
            dim="viewer_segment", op="eq", value="teens_13_17",
        )],
        confidence=0.9,
    )
    base.update(overrides)
    return CompiledScenario(**base)


def _verdict_response(
    *, score: float, gaps: list[str] = (), fixes: list[str] = (),
    accept_threshold: float = 0.7,
) -> dict:
    return {
        "content": [
            {
                "type": "tool_use",
                "name": "emit_verdict",
                "input": {
                    "score": score,
                    "rubric_breakdown": {
                        "schema_validity": score,
                        "perturbation_realism": score,
                        "simulator_constrainability": score,
                        "audience_filter_coherence": score,
                    },
                    "gaps": list(gaps),
                    "suggested_fixes": list(fixes),
                    "accept_threshold": accept_threshold,
                },
            }
        ]
    }


def _make_ctx(*responses) -> AsyncMock:
    ctx = AsyncMock()
    ctx.sample = AsyncMock(side_effect=list(responses))
    return ctx


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluator_returns_verdict():
    ctx = _make_ctx(_verdict_response(score=0.85, gaps=[], fixes=[]))
    verdict = await evaluate_scenario(scenario=_scenario(), ctx=ctx)
    assert isinstance(verdict, EvaluatorVerdict)
    assert verdict.score == pytest.approx(0.85)
    assert verdict.passes is True


# ---------------------------------------------------------------------------
# Sonnet — workers don't need Opus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluator_uses_sonnet():
    ctx = _make_ctx(_verdict_response(score=0.9))
    await evaluate_scenario(scenario=_scenario(), ctx=ctx)
    model = ctx.sample.call_args.kwargs.get("model", "")
    assert "sonnet" in model.lower()
    assert "opus" not in model.lower(), "evaluator is workers-class (PDF §7.4)"


# ---------------------------------------------------------------------------
# Strict tool use
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluator_strict_tool_use():
    ctx = _make_ctx(_verdict_response(score=0.9))
    await evaluate_scenario(scenario=_scenario(), ctx=ctx)
    kwargs = ctx.sample.call_args.kwargs
    tools = kwargs.get("tools") or []
    assert tools and tools[0]["name"] == "emit_verdict"
    assert tools[0]["strict"] is True
    assert kwargs.get("tool_choice") == {"type": "tool", "name": "emit_verdict"}


# ---------------------------------------------------------------------------
# Cache TTL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluator_caches_system_prompt_at_one_hour():
    ctx = _make_ctx(_verdict_response(score=0.9))
    await evaluate_scenario(scenario=_scenario(), ctx=ctx)
    system = ctx.sample.call_args.kwargs.get("system")
    assert isinstance(system, list) and system
    cache = system[0].get("cache_control", {})
    assert cache.get("type") == "ephemeral"
    assert cache.get("ttl") == "1h"


# ---------------------------------------------------------------------------
# passes property propagates correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluator_passes_property_below_threshold():
    ctx = _make_ctx(_verdict_response(score=0.6))
    verdict = await evaluate_scenario(scenario=_scenario(), ctx=ctx)
    assert verdict.passes is False


@pytest.mark.asyncio
async def test_evaluator_passes_property_above_threshold():
    ctx = _make_ctx(_verdict_response(score=0.8))
    verdict = await evaluate_scenario(scenario=_scenario(), ctx=ctx)
    assert verdict.passes is True


# ---------------------------------------------------------------------------
# No disk writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluator_does_not_write_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    ctx = _make_ctx(_verdict_response(score=0.9))
    await evaluate_scenario(scenario=_scenario(), ctx=ctx)
    after = set(tmp_path.iterdir())
    assert before == after


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluator_registered_readonly():
    from engine.server import engine_server
    tools = await engine_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert "evaluate_scenario" in by_name
    # Four tools after M15b: 2 helpers + compile + evaluate.
    expected = {
        "get_audience_definition", "lookup_seasonality",
        "compile_scenario", "evaluate_scenario",
    }
    assert expected <= set(by_name)
    assert by_name["evaluate_scenario"].annotations.readOnlyHint is True
