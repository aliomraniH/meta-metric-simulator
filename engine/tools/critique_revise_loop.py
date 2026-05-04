"""critique_revise_loop — Option C bounded iteration.

Per AGENTIC_ARCHITECTURE_INDEX.md §2.3 (Option C evaluator-optimizer)
and architecture_research.pdf §3.4: bound the compile→evaluate→revise
loop to N=2 iterations.  Beyond N=2 the synthesizer (M15c) commits
whatever the compiler last produced, marking it `disputed=true` if
the verdict is still failing.

This is **plain async helper code, not an `@mcp.tool`**.  Tools
expose capabilities to the LLM; this loop is internal orchestration
the synthesizer calls into.  Per the build prompt: *"Tools are how
the agent's capabilities are exposed to the LLM; the loop is internal
orchestration."*

Imports kept tight per AGENTIC_ARCHITECTURE_INDEX.md §8.1: typing,
the agentic schemas, and the two tools that compose this loop.  Do
NOT import from baselines/, curation/, calibration/.
"""
from __future__ import annotations

import logging
from typing import Any

from engine.schemas_agentic import CompiledScenario, EvaluatorVerdict
from engine.tools.compile_scenario import compile_scenario
from engine.tools.evaluate_scenario import evaluate_scenario

log = logging.getLogger(__name__)


async def critique_revise_loop(
    natural_language_intent: str,
    ctx: Any,
    max_iterations: int = 2,
    accept_threshold: float = 0.7,
) -> tuple[CompiledScenario, EvaluatorVerdict, int]:
    """Run the Option C evaluator-optimizer loop.

    Args:
        natural_language_intent:  the user's prompt, verbatim.
        ctx:                       FastMCP / MCP-sampling Context.
        max_iterations:            cap on compile→evaluate cycles (default 2).
        accept_threshold:          override the verdict's
                                   ``accept_threshold`` field (default 0.7).
                                   When the verdict's own threshold differs,
                                   we honour the stricter of the two so the
                                   caller-provided floor cannot be relaxed
                                   by a permissive evaluator.

    Returns:
        ``(final_scenario, final_verdict, iterations_used)``.

        * ``final_scenario``:    the most recent CompiledScenario.
        * ``final_verdict``:     the verdict against that scenario.
        * ``iterations_used``:   1-based count of iterations actually
                                 executed.  Equals ``max_iterations`` only
                                 when every iteration failed to clear
                                 ``accept_threshold``.

    The function never raises on a failing verdict — soft-mode is the
    correct outcome for genuinely contestable scenarios.  Pydantic
    validation errors from the underlying tools propagate as-is.
    """
    feedback: list[str] | None = None
    iterations_used = 0
    final_scenario: CompiledScenario | None = None
    final_verdict: EvaluatorVerdict | None = None

    for i in range(max_iterations):
        iterations_used = i + 1

        scenario = await compile_scenario(
            natural_language_intent=natural_language_intent,
            ctx=ctx,
            feedback=feedback,
        )
        verdict = await evaluate_scenario(scenario=scenario, ctx=ctx)

        final_scenario = scenario
        final_verdict = verdict

        # Honour the stricter of caller threshold and verdict threshold
        # so a permissive evaluator cannot relax the loop's gate.
        effective_threshold = max(accept_threshold, verdict.accept_threshold)
        if verdict.score >= effective_threshold:
            log.info(
                "critique_revise_loop accepted at iteration %d (score=%.3f, threshold=%.3f)",
                iterations_used, verdict.score, effective_threshold,
            )
            return final_scenario, final_verdict, iterations_used

        # Score below threshold.  Build feedback for the next iteration.
        feedback = list(verdict.suggested_fixes) + [
            f"Address gap: {g}" for g in verdict.gaps
        ]
        log.info(
            "critique_revise_loop iteration %d failed (score=%.3f); "
            "preparing %d feedback items for next pass",
            iterations_used, verdict.score, len(feedback),
        )

    # N iterations exhausted, score still below threshold.  The
    # synthesizer (M15c) inspects iterations_used + final_verdict.passes
    # to decide whether to write with disputed=true.
    assert final_scenario is not None  # guaranteed by max_iterations >= 1
    assert final_verdict is not None
    return final_scenario, final_verdict, iterations_used
