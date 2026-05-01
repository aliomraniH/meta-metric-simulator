"""synthesize_diff — the SOLE WRITER to curation/sources_registry.yaml.

Per architecture_research.pdf §10.3 and AGENTIC_ARCHITECTURE_INDEX.md
§2.4 (synthesize_diff → M13 → curation/sources_registry.yaml → Strict
(Option D) → 0.85 confidence floor).

The seven-step gate before any write reaches disk:

  1. @requires_calibration_unlocked: aborts on lock drift.
  2. Hardcoded target = curation/sources_registry.yaml.  Other targets
     are not even a parameter — synthesize_diff cannot be coerced
     into writing elsewhere.
  3. Constitutional check: any incoming flag at severity ≥ high
     short-circuits to decision='reject' WITHOUT a model call.
  4. Critique-revise loop (max N=2): the curation-critic LLM checks
     the diff against the constitution; on unresolved high-severity
     flags after N=2, decision='escalate' without writing.
  5. Calibration-impact pre-check (sources_registry.yaml is part of
     the calibration lock); on drift, ctx.elicit for explicit
     human approval to invalidate calibration.
  6. Diff-confirm via ctx.elicit (constitution Rule 3 — NEVER
     auto-apply).  On decline, decision='reject'.
  7. Atomic .tmp + os.rename write.

Persistence (always, regardless of accept/reject) — observations
table gets every flag (winning + losing); syntheses table gets the
final CurationDecision for the audit trail.

Cost: ~$0.02 per synthesis worst-case (Opus orchestrator + 2x Sonnet
critic rounds + Sonnet refresher).  System prompt cached at ttl="1h".
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiofiles
import ulid
import yaml
from pydantic import ValidationError

from calibration.lock import requires_calibration_unlocked, would_change_locked_hash
from curation.schemas import (
    ConstitutionFlag,
    CurationDecision,
    DiffProposal,
)
from curation.server import curation_server

log = logging.getLogger(__name__)


_REGISTRY_PATH = "curation/sources_registry.yaml"
_CRITIC_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "critic.md"
)
_REFRESHER_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "refresher.md"
)

_OPUS_MODEL = "claude-opus-4-7"
_SONNET_MODEL = "claude-sonnet-4-6"
_DEFAULT_THINKING_BUDGET = 8000  # synthesizer ID per orchestrator/agents.py
_DEFAULT_MAX_TOKENS = 4096
_CONFIDENCE_FLOOR = 0.85
_MAX_REVISE_ROUNDS = 2

_BLOCKING_SEVERITIES: frozenset[str] = frozenset({"high", "critical"})


# ---------------------------------------------------------------------------
# Public tool — NOT readOnlyHint; this is a writer
# ---------------------------------------------------------------------------

@curation_server.tool()
@requires_calibration_unlocked
async def synthesize_diff(
    diff: DiffProposal,
    constitution_flags: list[ConstitutionFlag],
    ctx: Any,
) -> CurationDecision:
    """Sole writer to curation/sources_registry.yaml.

    Args:
        diff:                  the curation-refresher's proposal.
        constitution_flags:    flags from sanitize_constitution (and any
                               accumulated from prior critique iterations).
        ctx:                   FastMCP / MCP-sampling Context.

    Returns:
        CurationDecision regardless of write outcome.  Always persisted
        to syntheses + observations tables for the audit trail.
    """
    # ---- 3. Constitutional short-circuit ----------------------------------
    blocking = [f for f in constitution_flags if f.severity in _BLOCKING_SEVERITIES]
    if blocking:
        decision = CurationDecision(
            decision="reject",
            confidence=0.0,
            rationale=(
                f"Incoming constitution_flags at severity ≥ high: "
                f"{[f.rule for f in blocking]}.  Refusing to engage critic; "
                "send back to refresher with revisions."
            ),
            rejection_flags=blocking,
        )
        await _persist_synthesis_decision(decision, diff, constitution_flags)
        await _persist_observation_flags(constitution_flags, diff)
        return decision

    # ---- 4. Critique-revise loop (Option D) -------------------------------
    accumulated_flags: list[ConstitutionFlag] = list(constitution_flags)
    current_diff = diff
    final_critic_flags: list[ConstitutionFlag] = []
    for round_idx in range(_MAX_REVISE_ROUNDS):
        critic_flags = await _run_critic(current_diff, ctx)
        accumulated_flags.extend(critic_flags)
        final_critic_flags = critic_flags
        unresolved = [f for f in critic_flags if f.severity in _BLOCKING_SEVERITIES]
        if not unresolved:
            break
        if round_idx == _MAX_REVISE_ROUNDS - 1:
            decision = CurationDecision(
                decision="escalate",
                confidence=0.0,
                rationale=(
                    f"Critique-revise exhausted after N={_MAX_REVISE_ROUNDS} rounds; "
                    f"unresolved flags: {[f.rule for f in unresolved]}.  Surfacing for "
                    "operator review."
                ),
                rejection_flags=unresolved,
            )
            await _persist_synthesis_decision(decision, diff, accumulated_flags)
            await _persist_observation_flags(accumulated_flags, diff)
            return decision
        current_diff = await _run_refresher_revision(current_diff, critic_flags, ctx)

    # ---- 5. Calibration-impact pre-check ----------------------------------
    drift = await asyncio.to_thread(
        would_change_locked_hash, _REGISTRY_PATH, current_diff.field, current_diff.proposed_value
    )
    if drift:
        cal_approve = await ctx.elicit(
            f"This curation diff would invalidate the calibration lock.\n"
            f"  registry_entry_id: {current_diff.registry_entry_id}\n"
            f"  field:             {current_diff.field}\n"
            f"  proposed_value:    {current_diff.proposed_value!r}\n"
            "Re-running calibration may produce different results. Approve?"
        )
        if not cal_approve:
            decision = CurationDecision(
                decision="reject",
                confidence=0.0,
                rationale="User declined calibration-invalidating change",
            )
            await _persist_synthesis_decision(decision, diff, accumulated_flags)
            await _persist_observation_flags(accumulated_flags, diff)
            return decision

    # ---- Opus judge: final accept/reject + confidence ---------------------
    judge_decision = await _run_judge(current_diff, accumulated_flags, ctx)

    # ---- 6. ctx.elicit for diff-confirm (constitution Rule 3) -------------
    if judge_decision.decision == "accept" and judge_decision.confidence >= _CONFIDENCE_FLOOR:
        diff_approve = await ctx.elicit(_render_diff_for_human(current_diff))
        if not diff_approve:
            judge_decision = CurationDecision(
                decision="reject",
                confidence=judge_decision.confidence,
                rationale="User declined diff via ctx.elicit",
            )

    # ---- Persist always ---------------------------------------------------
    await _persist_synthesis_decision(judge_decision, diff, accumulated_flags)
    await _persist_observation_flags(accumulated_flags, diff)

    # ---- 7. Atomic write only on accept + floor + approval ----------------
    if (
        judge_decision.decision == "accept"
        and judge_decision.confidence >= _CONFIDENCE_FLOOR
    ):
        await _apply_and_write_atomic(current_diff)
        log.info(
            "synthesize_diff wrote %s::%s = %r (confidence=%.2f)",
            current_diff.registry_entry_id, current_diff.field,
            current_diff.proposed_value, judge_decision.confidence,
        )
        # Re-emit with the applied_diff field set so callers see what landed.
        judge_decision = judge_decision.model_copy(
            update={"applied_diff": current_diff}
        )
    else:
        log.info(
            "synthesize_diff did NOT write %s::%s (decision=%s, confidence=%.2f)",
            current_diff.registry_entry_id, current_diff.field,
            judge_decision.decision, judge_decision.confidence,
        )

    return judge_decision


# ---------------------------------------------------------------------------
# Critique-revise helpers
# ---------------------------------------------------------------------------

async def _run_critic(diff: DiffProposal, ctx: Any) -> list[ConstitutionFlag]:
    """Sonnet 4.6 critic against curation/prompts/critic.md.

    Returns the parsed list of ConstitutionFlag.  Malformed responses
    are treated as zero-flag (the deterministic sanitize_constitution
    catches the load-bearing cases; this is the LLM backstop).
    """
    system = _cached_system_block(_CRITIC_PROMPT_PATH.read_text())
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "flags": {"type": "array", "items": ConstitutionFlag.model_json_schema()},
        },
        "required": ["flags"],
    }
    emit = {
        "name": "emit_flags",
        "description": "Emit ConstitutionFlag list",
        "input_schema": schema,
        "strict": True,
    }
    response = await ctx.sample(
        model=_SONNET_MODEL,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                "Critique this DiffProposal against the constitution:\n\n"
                f"{json.dumps(diff.model_dump(mode='json'), indent=2)}"
            ),
        }],
        tools=[emit],
        tool_choice={"type": "tool", "name": "emit_flags"},
        max_tokens=_DEFAULT_MAX_TOKENS,
    )
    payload = _extract_tool_input(response, "emit_flags")
    raw_flags = payload.get("flags") or []
    out: list[ConstitutionFlag] = []
    for f in raw_flags:
        try:
            out.append(ConstitutionFlag.model_validate(f))
        except ValidationError as exc:
            log.debug("critic emitted unparseable flag: %s", exc)
    return out


async def _run_refresher_revision(
    diff: DiffProposal, flags: list[ConstitutionFlag], ctx: Any
) -> DiffProposal:
    """Send the diff + flags back to the refresher (Sonnet 4.6) for revision.

    On parse failure we keep the original diff — the next critic round
    will re-flag it and exhaust the loop into escalate.
    """
    system = _cached_system_block(_REFRESHER_PROMPT_PATH.read_text())
    schema = DiffProposal.model_json_schema()
    emit = {
        "name": "emit_diff_proposal",
        "description": "Emit revised DiffProposal",
        "input_schema": schema,
        "strict": True,
    }
    response = await ctx.sample(
        model=_SONNET_MODEL,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                "Revise this DiffProposal in light of the critic's flags.\n\n"
                f"Current proposal:\n{json.dumps(diff.model_dump(mode='json'), indent=2)}\n\n"
                f"Critic flags:\n{json.dumps([f.model_dump(mode='json') for f in flags], indent=2)}"
            ),
        }],
        tools=[emit],
        tool_choice={"type": "tool", "name": "emit_diff_proposal"},
        max_tokens=_DEFAULT_MAX_TOKENS,
    )
    try:
        ti = _extract_tool_input(response, "emit_diff_proposal")
        return DiffProposal.model_validate(ti)
    except (ValueError, ValidationError) as exc:
        log.warning("refresher revision unparseable: %s", exc)
        return diff


async def _run_judge(
    diff: DiffProposal,
    accumulated_flags: list[ConstitutionFlag],
    ctx: Any,
) -> CurationDecision:
    """Opus 4.7 judge with thinking + strict tool use → CurationDecision."""
    system = _cached_system_block(_REFRESHER_PROMPT_PATH.read_text())
    schema = CurationDecision.model_json_schema()
    emit = {
        "name": "emit_decision",
        "description": "Emit final CurationDecision",
        "input_schema": schema,
        "strict": True,
    }
    payload = {
        "task": "synthesize_curation_diff",
        "diff": diff.model_dump(mode="json"),
        "accumulated_flags": [f.model_dump(mode="json") for f in accumulated_flags],
    }
    response = await ctx.sample(
        model=_OPUS_MODEL,
        system=system,
        messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
        tools=[emit],
        tool_choice={"type": "tool", "name": "emit_decision"},
        thinking={"type": "enabled", "budget_tokens": _DEFAULT_THINKING_BUDGET},
        max_tokens=_DEFAULT_MAX_TOKENS,
    )
    try:
        ti = _extract_tool_input(response, "emit_decision")
        return CurationDecision.model_validate(ti)
    except (ValueError, ValidationError) as exc:
        log.warning("synthesize_diff: malformed judge output: %s", exc)
        return CurationDecision(
            decision="reject",
            confidence=0.0,
            rationale=f"Malformed synthesizer output: {exc!s}",
        )


# ---------------------------------------------------------------------------
# Persistence helpers — best-effort, lazy infra.db imports
# ---------------------------------------------------------------------------

async def _persist_synthesis_decision(
    decision: CurationDecision,
    diff: DiffProposal,
    accumulated_flags: list[ConstitutionFlag],
) -> None:
    try:
        from infra.db import session as _session  # lazy
        from infra.db import syntheses as _syntheses_table
    except Exception as exc:  # noqa: BLE001
        log.debug("synthesis persistence skipped (no infra.db): %s", exc)
        return
    payload = {
        "synthesis_id": str(ulid.new()),
        "layer": "l5",
        "subject_id": f"{_REGISTRY_PATH}::{diff.registry_entry_id}.{diff.field}",
        "decision": decision.decision,
        "confidence": decision.confidence,
        "rationale": decision.rationale,
        "inputs": {
            "diff": diff.model_dump(mode="json"),
            "accumulated_flags": [f.model_dump(mode="json") for f in accumulated_flags],
        },
        "output": decision.model_dump(mode="json"),
        "disputed": 0,
        "trace_id": None,
        "created_at": time.time(),
    }
    try:
        async with _session() as s:
            await s.execute(_syntheses_table.insert(), [payload])
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("synthesis persistence failed: %s", exc)


async def _persist_observation_flags(
    accumulated_flags: list[ConstitutionFlag],
    diff: DiffProposal,
) -> None:
    try:
        from infra.db import session as _session
        from infra.db import observations as _observations_table
    except Exception as exc:  # noqa: BLE001
        log.debug("observations persistence skipped (no infra.db): %s", exc)
        return
    if not accumulated_flags:
        return
    rows = [
        {
            "observation_id": str(ulid.new()),
            "layer": "l5",
            "tool": "sanitize_constitution|critic",
            "subject_id": f"{_REGISTRY_PATH}::{diff.registry_entry_id}.{diff.field}",
            "kind": flag.rule,
            "severity": flag.severity,
            "evidence": {
                "evidence": list(flag.evidence),
                "suggested_fix": flag.suggested_fix,
            },
            "trace_id": None,
            "created_at": time.time(),
        }
        for flag in accumulated_flags
    ]
    try:
        async with _session() as s:
            await s.execute(_observations_table.insert(), rows)
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("observations persistence failed: %s", exc)


# ---------------------------------------------------------------------------
# YAML I/O — atomic write
# ---------------------------------------------------------------------------

def apply_diff_to_registry(content: dict[str, Any], diff: DiffProposal) -> dict[str, Any]:
    """Pure helper: mutate a *copy* of the registry dict to apply the diff.

    Returns a new dict so callers can serialize it to yaml.  The
    `field` value lands inside the entry block under
    `primary_sources.<registry_entry_id>.<field>`.  Section_refs and
    other top-level keys are preserved untouched.
    """
    out = dict(content) if isinstance(content, dict) else {}
    primary = dict(out.get("primary_sources") or {})
    entry = dict(primary.get(diff.registry_entry_id) or {})
    entry[diff.field] = diff.proposed_value
    primary[diff.registry_entry_id] = entry
    out["primary_sources"] = primary
    return out


async def _apply_and_write_atomic(diff: DiffProposal) -> None:
    p = Path(_REGISTRY_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        async with aiofiles.open(_REGISTRY_PATH, "r") as fh:
            text = await fh.read()
        doc = yaml.safe_load(text) or {}
    else:
        doc = {}
    new_doc = apply_diff_to_registry(doc, diff)
    serialized = yaml.safe_dump(new_doc, sort_keys=False)
    tmp_path = str(p) + ".tmp"
    async with aiofiles.open(tmp_path, "w") as fh:
        await fh.write(serialized)
    os.rename(tmp_path, str(p))


def _render_diff_for_human(diff: DiffProposal) -> str:
    return (
        "Approve this curation diff?\n"
        "──────────────────────────────────────────────\n"
        f"  registry_entry_id: {diff.registry_entry_id}\n"
        f"  field:             {diff.field}\n"
        f"  current_value:     {diff.current_value!r}\n"
        f"  proposed_value:    {diff.proposed_value!r}\n"
        f"  rationale:         {diff.rationale}\n"
        f"  cited_evidence:    {len(diff.cited_evidence)} citation(s)\n"
        "──────────────────────────────────────────────"
    )


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _cached_system_block(prompt: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": prompt,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def _content_blocks(response: Any) -> list[Any]:
    if response is None:
        return []
    if isinstance(response, dict):
        content = response.get("content")
    else:
        content = getattr(response, "content", None)
    if content is None:
        return []
    if isinstance(content, list):
        return list(content)
    return [content]


def _block_field(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _extract_tool_input(response: Any, tool_name: str) -> dict[str, Any]:
    for block in _content_blocks(response):
        if _block_field(block, "type") != "tool_use":
            continue
        if _block_field(block, "name") != tool_name:
            continue
        ti = _block_field(block, "input", {})
        if isinstance(ti, dict):
            return ti
        return {}
    raise ValueError(
        f"response did not contain a tool_use block named {tool_name!r}"
    )
