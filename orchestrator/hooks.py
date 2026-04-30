"""Hook factories for the sanitize/synthesize deliberation contract.

Two factories live here:

  make_sanitize_pretooluse_hook(contract)
      For strict mode.  Validates the tool call payload before execution:
        - required citation/confidence fields present
        - synthesizer confidence ≥ contract.confidence_floor
        - no sanitizer flag at or above contract.severity_block_threshold
      On fail, returns the SDK's permission-deny shape:
        { "hookSpecificOutput": {
              "hookEventName": "PreToolUse",
              "permissionDecision": "deny",
              "permissionDecisionReason": "..." }}

  make_synthesize_posttooluse_hook(contract)
      For soft mode.  Reads sanitizer flags from the tool result, computes
      the synthesizer's confidence (if not already attached), and rewrites
      `updatedMCPToolOutput` to carry {disputed, confidence, disputed_by}.

Both hooks are mode-agnostic at the call site — they read the contract for
the layer and apply the appropriate policy.  Telemetry events are emitted
for every gate decision so the audit trail is complete.

Payload shape conventions
-------------------------
The hooks expect tool payloads to follow these conventions:

  PreToolUse `tool_input`:
    {
      "synthesizer_confidence": float,  # required for strict-mode writers
      "sanitizer_flags": [               # produced by sanitize_* tools
        {"kind": str, "severity": str, "evidence": ..., ...}
      ],
      "citations": [...],                # required for strict-mode writers
      ...
    }

  PostToolUse `tool_response`:
    {
      "result": ...,                     # the tool's own return value
      "sanitizer_flags": [...],          # collected by upstream sanitizers
      "synthesizer_confidence": float,
      "synthesizer_id": str,             # which agent produced the result
    }
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from orchestrator.deliberation import DeliberationContract, severity_at_or_above

log = logging.getLogger(__name__)


# Per-tool-name lists are used by the SDK; each hook is an async callable.
HookFn = Callable[[dict, Any, Any], Awaitable[dict]]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _allow() -> dict:
    return {}


def _max_severity(flags: list[dict]) -> str:
    """Return the highest-severity string among the given flags, or 'info'."""
    best = "info"
    for f in flags:
        sev = f.get("severity", "info")
        if severity_at_or_above(sev, best):
            best = sev
    return best


def _telemetry_event(name: str, attrs: dict[str, Any]) -> None:
    """Best-effort span event; never raises, never blocks the hook."""
    try:
        from infra.telemetry import get_tracer
        from opentelemetry import trace
        tracer = get_tracer()
        if tracer is None:
            return
        span = trace.get_current_span()
        if span is not None and span.is_recording():
            span.add_event(name, attributes=attrs)
    except Exception:
        log.debug("telemetry event %s suppressed", name, exc_info=True)


# -----------------------------------------------------------------------------
# PreToolUse — strict-mode sanitize gate
# -----------------------------------------------------------------------------

def make_sanitize_pretooluse_hook(contract: DeliberationContract) -> HookFn:
    """Build a PreToolUse hook for the given (strict-mode) layer contract.

    Soft-mode contracts can also use this if a callsite wants belt-and-braces,
    but typically only strict layers wire it up.
    """

    async def hook(input_data: dict, tool_use_id: Any, context: Any) -> dict:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {}) or {}

        # Only gate writes — synthesize_* is the sole writer per the
        # single-writer rule.  Read-only sanitizer / extractor tools pass.
        if not tool_name.startswith("synthesize") and "synthesize" not in tool_name:
            return _allow()

        confidence = tool_input.get("synthesizer_confidence")
        flags: list[dict] = tool_input.get("sanitizer_flags", []) or []
        citations = tool_input.get("citations", []) or []

        # 1) Required fields for strict-mode writers.
        if contract.mode == "strict":
            if confidence is None:
                _telemetry_event(
                    "deliberation.deny",
                    {"layer": contract.layer, "reason": "missing_confidence", "tool": tool_name},
                )
                return _deny(
                    f"strict-mode {contract.layer}: tool {tool_name!r} requires "
                    "synthesizer_confidence in tool_input"
                )
            if not citations:
                _telemetry_event(
                    "deliberation.deny",
                    {"layer": contract.layer, "reason": "missing_citations", "tool": tool_name},
                )
                return _deny(
                    f"strict-mode {contract.layer}: tool {tool_name!r} requires "
                    "non-empty citations[] for write"
                )

        # 2) Confidence floor.
        if confidence is not None and contract.blocks_for_confidence(float(confidence)):
            _telemetry_event(
                "deliberation.deny",
                {
                    "layer": contract.layer,
                    "reason": "confidence_below_floor",
                    "confidence": float(confidence),
                    "floor": contract.confidence_floor,
                    "tool": tool_name,
                },
            )
            return _deny(
                f"strict-mode {contract.layer}: synthesizer confidence "
                f"{confidence:.2f} below floor {contract.confidence_floor:.2f}"
            )

        # 3) Sanitizer severity threshold.
        max_sev = _max_severity(flags)
        if contract.blocks_for_severity(max_sev):
            _telemetry_event(
                "deliberation.deny",
                {
                    "layer": contract.layer,
                    "reason": "severity_blocked",
                    "max_severity": max_sev,
                    "threshold": contract.severity_block_threshold,
                    "tool": tool_name,
                },
            )
            return _deny(
                f"strict-mode {contract.layer}: sanitizer flag at severity "
                f"{max_sev!r} ≥ block threshold {contract.severity_block_threshold!r}"
            )

        _telemetry_event(
            "deliberation.allow",
            {"layer": contract.layer, "tool": tool_name, "confidence": confidence},
        )
        return _allow()

    return hook


# -----------------------------------------------------------------------------
# PostToolUse — soft-mode synthesize annotator
# -----------------------------------------------------------------------------

def make_synthesize_posttooluse_hook(contract: DeliberationContract) -> HookFn:
    """Build a PostToolUse hook that rewrites tool output with dispute metadata.

    For soft mode (Layers 6 and 8) this is the primary gate.  For strict
    mode it's still useful as a belt-and-braces audit trail rewrite — the
    hook is mode-aware and only sets disputed=true under the right rules.
    """

    async def hook(input_data: dict, tool_use_id: Any, context: Any) -> dict:
        tool_name = input_data.get("tool_name", "")
        response = input_data.get("tool_response", {}) or {}

        # Only annotate writers; reads pass through unchanged.
        if "synthesize" not in tool_name:
            return {}

        flags: list[dict] = response.get("sanitizer_flags", []) or []
        confidence = response.get("synthesizer_confidence")
        synthesizer_id = response.get("synthesizer_id", "synthesizer")

        max_sev = _max_severity(flags)
        disputed = False
        reasons: list[str] = []

        if confidence is not None and float(confidence) < contract.confidence_floor:
            disputed = True
            reasons.append(f"confidence<{contract.confidence_floor:.2f}")
        if severity_at_or_above(max_sev, "high"):
            disputed = True
            reasons.append(f"max_severity={max_sev}")
        if any(f.get("kind") == "evaluator_exhausted" for f in flags):
            disputed = True
            reasons.append("evaluator_optimizer_exhausted")

        rewritten = {
            **response,
            "disputed": disputed,
            "confidence": confidence,
            "disputed_by": synthesizer_id if disputed else None,
            "dispute_reasons": reasons,
        }

        _telemetry_event(
            "deliberation.annotate",
            {
                "layer": contract.layer,
                "tool": tool_name,
                "disputed": disputed,
                "max_severity": max_sev,
                "confidence": confidence,
            },
        )

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedMCPToolOutput": rewritten,
            }
        }

    return hook


# -----------------------------------------------------------------------------
# Convenience: build the hooks payload the SDK expects, for a layer
# -----------------------------------------------------------------------------

def hooks_for_layer(layer: str) -> dict[str, list[HookFn]]:
    from orchestrator.deliberation import get as get_contract

    contract = get_contract(layer)
    return {
        "PreToolUse": [make_sanitize_pretooluse_hook(contract)],
        "PostToolUse": [make_synthesize_posttooluse_hook(contract)],
    }


# -----------------------------------------------------------------------------
# M12c-ii — layer-isolation PreToolUse hook
# -----------------------------------------------------------------------------

# Paths the agentic layers MUST NOT write to.  baselines/data/ is NOT in
# this list — synthesize_baseline writes there.  curation/ is excluded
# because synthesize_diff (M13) writes curation/sources_registry.yaml.
_DETERMINISTIC_PATH_PREFIXES: tuple[str, ...] = (
    "params/",
    "algorithms/",
    "engine/",
    "metrics/",
    "calibration/",
)

# Synthesize_* tool name suffixes from AGENTIC_ARCHITECTURE_INDEX.md §2.4.
_SYNTHESIZE_TOOL_SUFFIXES: tuple[str, ...] = (
    "synthesize_baseline",   # M12 — baselines/data/*.yaml
    "synthesize_diff",       # M13 — curation/sources_registry.yaml
    "synthesize_scenario",   # M15 — scenarios table
    "synthesize_insight",    # M16 — insights table
)


def make_layer_isolation_hook() -> HookFn:
    """Block agentic tool calls that try to write to deterministic-layer state.

    Per architecture_research.pdf §10.4 and AGENTIC_ARCHITECTURE_INDEX.md
    §8 rule 1.  Belt-and-braces with the per-tool target_file check
    inside each synthesize_* implementation — this hook runs at the
    SDK boundary, the per-tool check runs in-process.

    Logic:
      * Tools whose context carries readOnlyHint=True are allowed.
      * Any other tool whose target_file/path/file argument begins with
        a deterministic-path prefix (params/, algorithms/, engine/,
        metrics/, calibration/) is denied — including synthesize_* tools
        whose name passes the synthesize check but whose TARGET is
        outside their canonical scope.
    """

    async def hook(tool_name, tool_input=None, context=None) -> dict:
        # Normalise the call shape — the SDK passes (input_data, tool_use_id,
        # context) in some places and (tool_name, tool_input, context) in
        # others.  Tests typically pass the second form directly.
        if isinstance(tool_name, dict):
            input_data = tool_name
            tool_name_str = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {}) or {}
        else:
            tool_name_str = tool_name or ""
            tool_input = tool_input or {}
        context = context or {}

        # 1. Read-only tools always pass.
        annotations = (context or {}).get("tool_annotations", {}) or {}
        if annotations.get("readOnlyHint"):
            return {}

        # 2. Resolve a target path from common arg keys.
        target = (
            tool_input.get("target_file")
            or tool_input.get("path")
            or tool_input.get("file")
            or ""
        )
        if not isinstance(target, str):
            target = str(target)
        if not target:
            return {}  # no target → nothing to gate on

        # 3. Deny any write to a deterministic-path prefix.  This denies
        # both non-synthesizer writes AND synthesize_* writes that tried
        # to escape their canonical scope.
        for prefix in _DETERMINISTIC_PATH_PREFIXES:
            if target.startswith(prefix):
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Tool {tool_name_str!r} attempted write to deterministic "
                            f"layer prefix {prefix!r} (target={target!r}). Only "
                            "synthesize_* tools may write canonical state, and only "
                            "to their layer's canonical artefact. See "
                            "AGENTIC_ARCHITECTURE_INDEX.md §8 rule 2."
                        ),
                    }
                }

        return {}  # allow

    return hook
