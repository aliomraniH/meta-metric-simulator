"""Tests for engine/tools/critique_revise_loop.py.

The bounded N=2 evaluator-optimizer loop is exercised by patching
the inner `compile_scenario` and `evaluate_scenario` so we control
exactly which iteration passes / fails.  Tests do not stand up a
live ctx — the loop's job is to orchestrate the two tools, not to
re-test their own internals.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import engine.tools.critique_revise_loop as loop_module
from engine.schemas_agentic import (
    CompiledScenario,
    EvaluatorVerdict,
    Perturbation,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _scenario(name: str = "iter_scenario") -> CompiledScenario:
    return CompiledScenario(
        name=name,
        description="test",
        natural_language_intent="what if foo?",
        perturbations=[Perturbation(
            target="ad_load_pct", op="set", value=0.15, rationale="x",
        )],
        time_horizon="7d",
        audience_filters=[],
        confidence=0.8,
    )


def _verdict(score: float, *, gaps=(), fixes=(), threshold: float = 0.7) -> EvaluatorVerdict:
    return EvaluatorVerdict(
        score=score,
        rubric_breakdown={
            "schema_validity": score,
            "perturbation_realism": score,
            "simulator_constrainability": score,
            "audience_filter_coherence": score,
        },
        gaps=list(gaps),
        suggested_fixes=list(fixes),
        accept_threshold=threshold,
    )


@pytest.fixture
def patched_tools(monkeypatch):
    """Patch compile_scenario + evaluate_scenario at the loop module's
    import site so the loop sees our mocks.  Returns the two AsyncMocks
    so individual tests can configure side_effect."""
    compile_spy = AsyncMock()
    evaluate_spy = AsyncMock()
    monkeypatch.setattr(loop_module, "compile_scenario", compile_spy)
    monkeypatch.setattr(loop_module, "evaluate_scenario", evaluate_spy)
    return {"compile": compile_spy, "evaluate": evaluate_spy}


# ---------------------------------------------------------------------------
# 1. Accept on iteration 1 — no revision needed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_accepts_on_first_iteration(patched_tools):
    patched_tools["compile"].side_effect = [_scenario("iter1")]
    patched_tools["evaluate"].side_effect = [_verdict(0.9)]

    ctx = AsyncMock()
    scn, verdict, used = await loop_module.critique_revise_loop(
        natural_language_intent="x", ctx=ctx,
    )
    assert used == 1
    assert verdict.passes is True
    assert scn.name == "iter1"
    assert patched_tools["compile"].await_count == 1
    assert patched_tools["evaluate"].await_count == 1


# ---------------------------------------------------------------------------
# 2. Accept on iteration 2 — revise once, then pass
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_accepts_on_second_iteration(patched_tools):
    patched_tools["compile"].side_effect = [_scenario("iter1"), _scenario("iter2")]
    patched_tools["evaluate"].side_effect = [
        _verdict(0.5, gaps=["ad_load too high"], fixes=["set ad_load_pct ≤ 0.25"]),
        _verdict(0.85),
    ]

    ctx = AsyncMock()
    scn, verdict, used = await loop_module.critique_revise_loop(
        natural_language_intent="x", ctx=ctx,
    )
    assert used == 2
    assert verdict.passes is True
    assert scn.name == "iter2"
    # Second compile call carried the feedback from iteration 1.
    iter2_kwargs = patched_tools["compile"].await_args_list[1].kwargs
    assert iter2_kwargs.get("feedback") is not None
    assert any("ad_load_pct" in f for f in iter2_kwargs["feedback"])


# ---------------------------------------------------------------------------
# 3. Exhaust N=2 — return last failing scenario, no exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_exhausts_after_n2_failure(patched_tools):
    patched_tools["compile"].side_effect = [_scenario("iter1"), _scenario("iter2")]
    patched_tools["evaluate"].side_effect = [
        _verdict(0.4, gaps=["g1"], fixes=["f1"]),
        _verdict(0.5, gaps=["g2"], fixes=["f2"]),
    ]

    ctx = AsyncMock()
    scn, verdict, used = await loop_module.critique_revise_loop(
        natural_language_intent="x", ctx=ctx,
    )
    assert used == 2
    assert verdict.passes is False
    # The synthesizer (M15c) inspects this state and writes
    # disputed=true; the loop itself returns the last attempt.
    assert scn.name == "iter2"
    assert verdict.score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 4. Feedback wiring — iter 1 None, iter 2 carries fixes + gaps
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_passes_feedback_correctly(patched_tools):
    patched_tools["compile"].side_effect = [_scenario("iter1"), _scenario("iter2")]
    patched_tools["evaluate"].side_effect = [
        _verdict(0.4, gaps=["incoherent filter"], fixes=["use 'teens' not raw triple"]),
        _verdict(0.9),
    ]

    ctx = AsyncMock()
    await loop_module.critique_revise_loop(natural_language_intent="x", ctx=ctx)

    # Iteration 1: compile called with feedback=None.
    iter1_kwargs = patched_tools["compile"].await_args_list[0].kwargs
    assert iter1_kwargs.get("feedback") is None

    # Iteration 2: feedback contains BOTH suggested_fixes and "Address gap" entries.
    iter2_kwargs = patched_tools["compile"].await_args_list[1].kwargs
    fb = iter2_kwargs.get("feedback") or []
    assert any("use 'teens'" in f for f in fb)
    assert any("Address gap: incoherent filter" in f for f in fb)


# ---------------------------------------------------------------------------
# 5. max_iterations override is honoured
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_respects_max_iterations_param(patched_tools):
    patched_tools["compile"].side_effect = [
        _scenario("iter1"), _scenario("iter2"), _scenario("iter3"),
    ]
    patched_tools["evaluate"].side_effect = [
        _verdict(0.3), _verdict(0.4), _verdict(0.5),
    ]

    ctx = AsyncMock()
    scn, verdict, used = await loop_module.critique_revise_loop(
        natural_language_intent="x", ctx=ctx, max_iterations=3,
    )
    assert used == 3
    assert verdict.passes is False
    assert scn.name == "iter3"


# ---------------------------------------------------------------------------
# 6. accept_threshold override gates pass/fail correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_passes_threshold_to_decision_below(patched_tools):
    """At caller threshold 0.8, score 0.75 must NOT pass even when
    the verdict's own threshold is the default 0.7."""
    patched_tools["compile"].side_effect = [_scenario("iter1"), _scenario("iter2")]
    patched_tools["evaluate"].side_effect = [
        _verdict(0.75, threshold=0.7),  # would pass at 0.7, but caller asks for 0.8
        _verdict(0.75, threshold=0.7),
    ]

    ctx = AsyncMock()
    scn, verdict, used = await loop_module.critique_revise_loop(
        natural_language_intent="x", ctx=ctx, accept_threshold=0.8,
    )
    assert used == 2  # both iterations rejected by stricter caller threshold
    # final_verdict.passes uses the verdict's own threshold (0.7) so it
    # appears as True there, but the loop did NOT exit early — proving
    # the caller's stricter threshold gated the loop.
    assert patched_tools["compile"].await_count == 2


@pytest.mark.asyncio
async def test_loop_passes_threshold_to_decision_above(patched_tools):
    """At caller threshold 0.8, score 0.81 should pass on iteration 1."""
    patched_tools["compile"].side_effect = [_scenario("iter1")]
    patched_tools["evaluate"].side_effect = [_verdict(0.81, threshold=0.7)]

    ctx = AsyncMock()
    scn, verdict, used = await loop_module.critique_revise_loop(
        natural_language_intent="x", ctx=ctx, accept_threshold=0.8,
    )
    assert used == 1
    assert patched_tools["compile"].await_count == 1
