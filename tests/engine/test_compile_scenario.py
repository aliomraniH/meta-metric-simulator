"""Tests for engine/tools/compile_scenario.py.

The compiler is exercised against a mocked ctx.sample.  The Sonnet
4.6 + strict-tool-use machinery and the cached system prompt are
asserted structurally so a future "simplification" cannot regress
the architectural contract.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from engine.schemas_agentic import CompiledScenario
from engine.tools.compile_scenario import compile_scenario


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scenario_input(**overrides) -> dict:
    base = dict(
        name="teen_reels_only_90d",
        description="All session time on Reels for teens, 90d.",
        natural_language_intent="what if Meta launched Reels-only mode for teens for 90 days?",
        perturbations=[{
            "target": "reels_share_of_time",
            "op": "set",
            "value": 1.0,
            "rationale": "Reels-only = 100%.",
        }],
        time_horizon="90d",
        audience_filters=[{
            "dim": "viewer_segment", "op": "eq", "value": "teens_13_17",
        }],
        seed=42,
        confidence=0.92,
    )
    base.update(overrides)
    return base


def _tool_use_response(tool_input: dict) -> dict:
    return {
        "content": [
            {
                "type": "tool_use",
                "name": "emit_compiled_scenario",
                "input": tool_input,
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
async def test_compiler_returns_compiled_scenario():
    ctx = _make_ctx(_tool_use_response(_scenario_input()))
    result = await compile_scenario(
        natural_language_intent="what if Meta launched Reels-only mode for teens for 90 days?",
        ctx=ctx,
    )
    assert isinstance(result, CompiledScenario)
    assert result.time_horizon == "90d"
    assert len(result.perturbations) == 1
    assert result.perturbations[0].target == "reels_share_of_time"


# ---------------------------------------------------------------------------
# Sonnet model selection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compiler_uses_sonnet():
    ctx = _make_ctx(_tool_use_response(_scenario_input()))
    await compile_scenario(natural_language_intent="x", ctx=ctx)
    kwargs = ctx.sample.call_args.kwargs
    model = kwargs.get("model", "")
    assert "sonnet" in model.lower(), f"expected Sonnet, got {model!r}"
    assert "opus" not in model.lower(), "compiler is a worker; must not use Opus (PDF §7.4)"


# ---------------------------------------------------------------------------
# Strict tool use
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compiler_strict_tool_use():
    ctx = _make_ctx(_tool_use_response(_scenario_input()))
    await compile_scenario(natural_language_intent="x", ctx=ctx)
    kwargs = ctx.sample.call_args.kwargs
    tools = kwargs.get("tools") or []
    assert tools and tools[0]["name"] == "emit_compiled_scenario"
    assert tools[0]["strict"] is True
    assert kwargs.get("tool_choice") == {"type": "tool", "name": "emit_compiled_scenario"}


# ---------------------------------------------------------------------------
# System-prompt cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compiler_caches_system_prompt_at_one_hour():
    """PDF §6.1: every cache_control block must explicitly set ttl='1h'."""
    ctx = _make_ctx(_tool_use_response(_scenario_input()))
    await compile_scenario(natural_language_intent="x", ctx=ctx)
    kwargs = ctx.sample.call_args.kwargs
    system = kwargs.get("system")
    assert isinstance(system, list) and system
    cache = system[0].get("cache_control", {})
    assert cache.get("type") == "ephemeral"
    assert cache.get("ttl") == "1h"


# ---------------------------------------------------------------------------
# Feedback round-trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compiler_includes_feedback_on_revision():
    """The user message on a revision pass must include each feedback string."""
    ctx = _make_ctx(_tool_use_response(_scenario_input()))
    await compile_scenario(
        natural_language_intent="x",
        ctx=ctx,
        feedback=["fix audience filter", "narrower horizon"],
    )
    kwargs = ctx.sample.call_args.kwargs
    user_message = kwargs["messages"][0]["content"]
    assert "fix audience filter" in user_message
    assert "narrower horizon" in user_message


@pytest.mark.asyncio
async def test_compiler_no_feedback_omits_revision_block():
    ctx = _make_ctx(_tool_use_response(_scenario_input()))
    await compile_scenario(natural_language_intent="x", ctx=ctx)
    user_message = ctx.sample.call_args.kwargs["messages"][0]["content"]
    assert "Address every gap" not in user_message


# ---------------------------------------------------------------------------
# Provenance preservation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compiler_backfills_natural_language_intent():
    """If the model dropped natural_language_intent, the tool backfills it."""
    payload = _scenario_input()
    payload.pop("natural_language_intent", None)
    ctx = _make_ctx(_tool_use_response(payload))
    intent = "what if x?"
    result = await compile_scenario(natural_language_intent=intent, ctx=ctx)
    assert result.natural_language_intent == intent


# ---------------------------------------------------------------------------
# No disk writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compiler_does_not_write_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    ctx = _make_ctx(_tool_use_response(_scenario_input()))
    await compile_scenario(natural_language_intent="x", ctx=ctx)
    after = set(tmp_path.iterdir())
    assert before == after, f"compiler wrote files: {after - before}"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compiler_registered_readonly():
    from engine.server import engine_server
    tools = await engine_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert "compile_scenario" in by_name
    # The compiler proposes; the synthesizer (M15c) writes.
    assert by_name["compile_scenario"].annotations.readOnlyHint is True
