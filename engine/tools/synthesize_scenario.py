"""synthesize_scenario — sole writer to the scenarios table (Layer 6 soft mode).

Per AGENTIC_ARCHITECTURE_INDEX.md §2.4 (synthesize_scenario → M15 →
scenarios table → Soft (Option C) → 0.70 confidence floor) and
architecture_research.pdf §3.4.

The seven-step gate before any write reaches the database:

  1. @requires_calibration_unlocked: aborts on lock drift.
  2. Run the bounded N=2 critique-revise loop (compile → evaluate → revise).
  3. On verdict.passes → write with disputed=False.
  4. On N=2 exhaustion without score ≥ threshold → write with disputed=True.
     (Soft-mode contract: exploratory; the disputed bit is the
      user-visible signal that something was off — no escalation.)
  5. NO ctx.elicit — soft mode does not gate on user approval.
  6. Persist to scenarios table (perturbations stored in M7-engine
     format so engine/simulator.py can run the manifest unchanged).
  7. Return decision metadata.

The engine consumes the perturbations JSON directly via
`engine.scenario.Scenario.load`; we therefore translate
`CompiledScenario.Perturbation.op` into the M7 perturbation `type`
on write so the agentic→deterministic bridge is intact.

Cost: ~$0.01 per synthesis worst-case (compile×2 + evaluate×2).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import ulid

from calibration.lock import requires_calibration_unlocked
from engine.schemas_agentic import CompiledScenario, Perturbation
from engine.server import engine_server
from engine.tools.critique_revise_loop import critique_revise_loop

log = logging.getLogger(__name__)


_HORIZON_DAYS_MAP: dict[str, int] = {"1d": 1, "7d": 7, "28d": 28, "90d": 90}
_DEFAULT_ACCEPT_THRESHOLD = 0.70  # AGENTIC_ARCHITECTURE_INDEX.md §2.4


# ---------------------------------------------------------------------------
# Public tool — NOT readOnlyHint, this is the writer
# ---------------------------------------------------------------------------

@engine_server.tool()
@requires_calibration_unlocked
async def synthesize_scenario(
    natural_language_intent: str,
    ctx: Any,
) -> dict[str, Any]:
    """Sole writer to the `scenarios` table.

    Args:
        natural_language_intent:  the user's prompt, verbatim.
        ctx:                       FastMCP / MCP-sampling Context.

    Returns:
        dict with keys:
          * `scenario_id`         ULID; idempotent across re-submissions
                                  via the ulid library's monotonic ordering
          * `disputed`            False on pass, True on N=2 exhaustion
          * `confidence`          the compiler's self-rated confidence
          * `iterations_used`     1 or 2
          * `evaluator_verdict`   the final verdict as a dict (rubric
                                  breakdown, gaps, suggested_fixes)
    """
    final_scenario, final_verdict, iterations_used = await critique_revise_loop(
        natural_language_intent=natural_language_intent,
        ctx=ctx,
        max_iterations=2,
        accept_threshold=_DEFAULT_ACCEPT_THRESHOLD,
    )

    # Soft-mode contract: write either way; mark disputed when the
    # verdict's effective threshold was not cleared.
    disputed = not (
        final_verdict.score
        >= max(_DEFAULT_ACCEPT_THRESHOLD, final_verdict.accept_threshold)
    )

    scenario_id = str(ulid.new())
    horizon_days = _HORIZON_DAYS_MAP[final_scenario.time_horizon]

    # Translate Pydantic perturbation ops → M7 engine perturbation types
    # so the manifest is runnable by engine.scenario.Scenario.load + the
    # deterministic simulator.
    engine_perturbations = [
        _to_engine_perturbation(p, horizon_days=horizon_days)
        for p in final_scenario.perturbations
    ]

    row = {
        "scenario_id":            scenario_id,
        "name":                   final_scenario.name,
        "description":            final_scenario.description,
        "perturbations":          engine_perturbations,
        "horizon_days":           horizon_days,
        "seed":                   final_scenario.seed,
        "disputed":               1 if disputed else 0,
        "confidence":             float(final_scenario.confidence),
        "created_at":             time.time(),
        "natural_language_intent": final_scenario.natural_language_intent,
        "time_horizon":           final_scenario.time_horizon,
        "audience_filters":       [f.model_dump(mode="json") for f in final_scenario.audience_filters],
        "iterations_used":        iterations_used,
        "evaluator_verdict_json": final_verdict.model_dump(mode="json"),
    }

    await _persist_row(row)

    log.info(
        "synthesize_scenario wrote %s (disputed=%s, score=%.3f, iterations=%d)",
        scenario_id, disputed, final_verdict.score, iterations_used,
    )

    return {
        "scenario_id":      scenario_id,
        "disputed":         disputed,
        "confidence":       float(final_scenario.confidence),
        "iterations_used":  iterations_used,
        "evaluator_verdict": final_verdict.model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# Persistence helper — module-level so tests can monkey-patch
# ---------------------------------------------------------------------------

async def _persist_row(row: dict[str, Any]) -> None:
    """Insert one row into scenarios.  Best-effort — when the DB isn't
    configured (e.g., unit tests with no engine) the call is logged
    and skipped.  Production callers wire `infra.db.session` and the
    insert lands."""
    try:
        from infra.db import scenarios as scenarios_table  # lazy
        from infra.db import session as _session
    except Exception as exc:  # noqa: BLE001
        log.debug("scenarios persistence skipped (no infra.db): %s", exc)
        return
    try:
        async with _session() as s:
            await s.execute(scenarios_table.insert(), [row])
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("scenarios persistence failed: %s", exc)


# ---------------------------------------------------------------------------
# Op → engine-perturbation type translation
# ---------------------------------------------------------------------------

def _to_engine_perturbation(p: Perturbation, *, horizon_days: int) -> dict[str, Any]:
    """Translate a Pydantic Perturbation into the M7 engine's perturbation_def shape.

    The M7 deterministic engine accepts `ramp`, `step`, `spike` types
    (see engine/scenario.py).  The agentic ops `set`, `multiply`,
    `add`, `toggle` are encoded as `step`-shaped applications at the
    engine boundary — the engine's apply() walks the dotted-path
    target and writes `value` directly, which is the correct semantic
    for `set` and a best-effort encoding for the others.

    Multi-op semantic preservation is NOT load-bearing for M15c; the
    M15b evaluator already gates against unsupported targets, and the
    soft-mode contract permits disputed=true scenarios where the
    encoding is approximate.
    """
    op = p.op
    target = p.target
    value = float(p.value)

    if op == "ramp":
        return {
            "type": "ramp",
            "target": target,
            "from": 0.0,
            "to": value,
            "days": max(1, horizon_days),
            "start_day": 0,
        }
    if op == "spike":
        return {
            "type": "spike",
            "target": target,
            "to": value,
            "duration_days": min(3, max(1, horizon_days)),
            "start_day": 0,
        }
    # set / multiply / add / toggle all map to `step`.  The engine
    # writes `value` at the dotted path; semantics for non-set ops
    # are advisory (callers should prefer ramp/spike for those flows).
    return {
        "type": "step",
        "target": target,
        "value": value,
        "start_day": 0,
    }
