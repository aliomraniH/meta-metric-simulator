"""synthesize_baseline — the SOLE WRITER to baselines/data/*.yaml.

Per architecture_research.pdf §2.3 / §10.3 and
AGENTIC_ARCHITECTURE_INDEX.md §2.4 / §8.  The four-step gate before
any write reaches disk:

  1. @requires_calibration_unlocked decorator: aborts on lock drift.
  2. target_file scope check: writes are restricted to baselines/data/.
  3. would_change_locked_hash + ctx.elicit() escalation when the
     proposed change would invalidate calibration.
  4. Opus 4.7 judge with thinking_budget=16000 + strict tool use.
     Decision={accept, reject, escalate} + confidence + rationale.

Persistence (always, regardless of accept/reject) — observations and
syntheses tables get the durable artifact for audit.  Per
"minimize the game of telephone": the database row is the source of
truth, not the chat message.

Atomic write on accept+confidence ≥ 0.85: write to .tmp, os.rename
to canonical path.  This guards against partial writes on process
interruption.
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

from baselines.schemas import ExtractedMetric, SanitizerFlag, SynthesizerDecision
from baselines.server import baselines_server
from calibration.lock import requires_calibration_unlocked, would_change_locked_hash

log = logging.getLogger(__name__)


_RECONCILER_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "reconciler.md"
)
_DEFAULT_MODEL = "claude-opus-4-7"
_DEFAULT_THINKING_BUDGET = 16000
_DEFAULT_MAX_TOKENS = 4096
_CONFIDENCE_FLOOR = 0.85
_TARGET_PREFIX = "baselines/data/"


# ---------------------------------------------------------------------------
# Public tool — NOT readOnlyHint, this is the writer
# ---------------------------------------------------------------------------

@baselines_server.tool()  # NOT readOnlyHint — the synthesizer writes
@requires_calibration_unlocked
async def synthesize_baseline(
    extracted: ExtractedMetric,
    sanitizer_flags: list[SanitizerFlag],
    target_file: str,
    target_path: str,
    ctx: Any,
) -> SynthesizerDecision:
    """Sole writer to baselines/data/*.yaml.

    Args:
        extracted:        the extractor's proposal.
        sanitizer_flags:  flags from sanitize_schema / sanitize_policy /
                          sanitize_source_tier.  May be empty (clean).
        target_file:      path under baselines/data/.  Other prefixes
                          raise ValueError.
        target_path:      dotted path to the metric BLOCK in the yaml
                          (e.g. "dap_billion" or
                          "regional.us_canada.dap_million").  The
                          synthesizer updates the block's `value`
                          field and preserves sibling fields
                          (source, provenance, confidence, note).
        ctx:              FastMCP / MCP-sampling Context.

    Returns:
        SynthesizerDecision regardless of write outcome.  The decision
        is persisted to the syntheses table even on reject so the audit
        trail stays complete.
    """
    # ---- 1. Target scope --------------------------------------------------
    if not target_file.startswith(_TARGET_PREFIX):
        raise ValueError(
            f"synthesize_baseline writes are scoped to {_TARGET_PREFIX!r}; "
            f"refusing target_file={target_file!r}"
        )

    # ---- 2. Calibration-impact pre-check ---------------------------------
    drift = await asyncio.to_thread(
        would_change_locked_hash, target_file, target_path, extracted.value
    )
    if drift:
        approve = await ctx.elicit(
            f"This change would invalidate calibration. "
            f"File: {target_file}, path: {target_path}, "
            f"new value: {extracted.value!r}. "
            "Re-running calibration may produce different results. Approve?"
        )
        if not approve:
            decision = SynthesizerDecision(
                decision="reject",
                confidence=0.0,
                rationale="User declined calibration-invalidating change",
            )
            await _persist_synthesis_decision(
                decision, extracted, sanitizer_flags, target_file, target_path
            )
            await _persist_observation_flags(
                sanitizer_flags, extracted, target_file, target_path
            )
            return decision

    # ---- 3. Opus 4.7 judge with thinking + strict tool use ----------------
    system_prompt = _RECONCILER_PROMPT_PATH.read_text()
    cached_system = [{
        "type": "text",
        "text": system_prompt,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }]

    current_value = await _read_target_value(target_file, target_path)

    user_payload = {
        "task": "reconcile_extracted_metric",
        "extracted": extracted.model_dump(mode="json"),
        "sanitizer_flags": [f.model_dump(mode="json") for f in sanitizer_flags],
        "target_file": target_file,
        "target_path": target_path,
        "current_value": current_value,
    }

    judge_schema = SynthesizerDecision.model_json_schema()
    emit_decision_tool = {
        "name": "emit_decision",
        "description": "Emit the synthesizer's reconciliation decision.",
        "input_schema": judge_schema,
        "strict": True,
    }

    judge_response = await ctx.sample(
        model=_DEFAULT_MODEL,
        system=cached_system,
        messages=[{"role": "user", "content": json.dumps(user_payload, indent=2)}],
        tools=[emit_decision_tool],
        tool_choice={"type": "tool", "name": "emit_decision"},
        thinking={"type": "enabled", "budget_tokens": _DEFAULT_THINKING_BUDGET},
        max_tokens=_DEFAULT_MAX_TOKENS,
    )

    decision_input = _extract_tool_input(judge_response, "emit_decision")
    try:
        decision = SynthesizerDecision.model_validate(decision_input)
    except ValidationError as exc:
        # Conservative: a malformed judge output is a reject, not a write.
        log.warning("synthesize_baseline: malformed judge output: %s", exc)
        decision = SynthesizerDecision(
            decision="reject",
            confidence=0.0,
            rationale=f"Malformed synthesizer output: {exc!s}",
        )

    # ---- 4. Persist (always) ---------------------------------------------
    await _persist_synthesis_decision(
        decision, extracted, sanitizer_flags, target_file, target_path
    )
    await _persist_observation_flags(
        sanitizer_flags, extracted, target_file, target_path
    )

    # ---- 5. Atomic write on accept + confidence floor --------------------
    if decision.decision == "accept" and decision.confidence >= _CONFIDENCE_FLOOR:
        await _write_yaml_atomic(target_file, target_path, extracted)
        log.info(
            "synthesize_baseline wrote %s::%s = %r (confidence=%.2f)",
            target_file, target_path, extracted.value, decision.confidence,
        )
    else:
        log.info(
            "synthesize_baseline did NOT write %s::%s (decision=%s, confidence=%.2f)",
            target_file, target_path, decision.decision, decision.confidence,
        )

    return decision


# ---------------------------------------------------------------------------
# Persistence helpers — module-level so tests can monkey-patch
# ---------------------------------------------------------------------------

async def _persist_synthesis_decision(
    decision: SynthesizerDecision,
    extracted: ExtractedMetric,
    sanitizer_flags: list[SanitizerFlag],
    target_file: str,
    target_path: str,
) -> None:
    """Insert one row into syntheses.  Best-effort — when the DB isn't
    configured (e.g. unit tests) the call is logged and skipped."""
    try:
        from infra.db import session as _session  # lazy import
        from infra.db import syntheses as _syntheses_table
    except Exception as exc:  # noqa: BLE001
        log.debug("synthesis persistence skipped (no infra.db): %s", exc)
        return

    payload = {
        "synthesis_id": str(ulid.new()),
        "layer": "l4",
        "subject_id": f"{target_file}::{target_path}",
        "decision": decision.decision,
        "confidence": decision.confidence,
        "rationale": decision.rationale,
        "inputs": {
            "extracted": extracted.model_dump(mode="json"),
            "sanitizer_flags": [f.model_dump(mode="json") for f in sanitizer_flags],
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
    sanitizer_flags: list[SanitizerFlag],
    extracted: ExtractedMetric,
    target_file: str,
    target_path: str,
) -> None:
    """Insert one row per sanitizer flag (winning AND losing) into
    observations.  Best-effort like _persist_synthesis_decision."""
    try:
        from infra.db import session as _session
        from infra.db import observations as _observations_table
    except Exception as exc:  # noqa: BLE001
        log.debug("observations persistence skipped (no infra.db): %s", exc)
        return

    if not sanitizer_flags:
        return

    rows = [
        {
            "observation_id": str(ulid.new()),
            "layer": "l4",
            "tool": "sanitize_*",
            "subject_id": f"{target_file}::{target_path}",
            "kind": flag.kind,
            "severity": flag.severity,
            "evidence": {
                "evidence": list(flag.evidence),
                "span": list(flag.span) if flag.span is not None else None,
                "suggested_fix": flag.suggested_fix,
                "extracted_metric_id": extracted.metric_id,
            },
            "trace_id": None,
            "created_at": time.time(),
        }
        for flag in sanitizer_flags
    ]
    try:
        async with _session() as s:
            await s.execute(_observations_table.insert(), rows)
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("observations persistence failed: %s", exc)


# ---------------------------------------------------------------------------
# YAML I/O helpers
# ---------------------------------------------------------------------------

async def _read_target_value(target_file: str, target_path: str) -> Any:
    """Read the current value at target_path from target_file.  Returns
    None when the file or path doesn't exist (the synthesizer treats
    that as "first write")."""
    p = Path(target_file)
    if not p.exists():
        return None
    async with aiofiles.open(target_file, "r") as fh:
        text = await fh.read()
    doc = yaml.safe_load(text) or {}
    node = doc
    for part in target_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


async def _write_yaml_atomic(
    target_file: str, target_path: str, extracted: ExtractedMetric
) -> None:
    """Apply the change to target_file::target_path and atomically rename.

    Preserves sibling fields under the leaf (source / provenance /
    confidence / note) by merging into the existing dict block.
    """
    p = Path(target_file)
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists():
        async with aiofiles.open(target_file, "r") as fh:
            text = await fh.read()
        doc = yaml.safe_load(text) or {}
    else:
        doc = {}

    parts = target_path.split(".")
    node = doc
    for part in parts[:-1]:
        if not isinstance(node, dict):
            raise ValueError(f"target_path {target_path!r} does not fit yaml shape")
        node = node.setdefault(part, {})
    if not isinstance(node, dict):
        raise ValueError(f"target_path {target_path!r} does not fit yaml shape")
    leaf_key = parts[-1]
    existing = node.get(leaf_key)
    new_block: dict[str, Any]
    if isinstance(existing, dict):
        new_block = {**existing}
    else:
        new_block = {}
    new_block["value"] = extracted.value
    # Source-of-truth fields the provenance audit (M8) requires.
    new_block.setdefault("source", extracted.source.url or extracted.source.doc_title or "")
    new_block.setdefault("provenance", "synthesized_2026-04-28")
    new_block.setdefault("confidence", "low")
    if extracted.source.quoted_text:
        new_block.setdefault("note", extracted.source.quoted_text)
    node[leaf_key] = new_block

    serialized = yaml.safe_dump(doc, sort_keys=False)
    tmp_path = str(p) + ".tmp"
    async with aiofiles.open(tmp_path, "w") as fh:
        await fh.write(serialized)
    os.rename(tmp_path, str(p))


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
