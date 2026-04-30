"""Tests for orchestrator/hooks.py::make_layer_isolation_hook.

The layer-isolation PreToolUse hook is the SDK-boundary half of the
"synthesize_* is the sole writer to canonical state" rule (per
AGENTIC_ARCHITECTURE_INDEX.md §8 rules 1+2 / architecture_research.pdf
§10.4).  The in-tool target_file check inside synthesize_baseline is
the in-process half — these belt-and-braces guards protect against
prompt-injection-driven escapes.
"""
from __future__ import annotations

import pytest

from orchestrator.hooks import make_layer_isolation_hook


def _is_deny(result: dict) -> bool:
    return (
        bool(result)
        and result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


# ---------------------------------------------------------------------------
# 1. Read-only tools always pass
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hook_allows_read_only_tools():
    hook = make_layer_isolation_hook()

    # Even though target_file lives under params/, readOnlyHint=True passes.
    result = await hook(
        "sanitize_schema",
        {"target_file": "params/segment_propensities.yaml"},
        {"tool_annotations": {"readOnlyHint": True}},
    )
    assert not _is_deny(result), "readOnlyHint=True must not be denied"


# ---------------------------------------------------------------------------
# 2. Non-synthesize writes to deterministic prefixes are blocked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hook_blocks_non_synthesize_writing_to_params():
    hook = make_layer_isolation_hook()
    result = await hook(
        "rogue_tool",
        {"target_file": "params/segment_propensities.yaml"},
        {},  # no readOnlyHint
    )
    assert _is_deny(result)
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "params/" in reason


@pytest.mark.asyncio
async def test_hook_blocks_non_synthesize_writing_to_calibration():
    hook = make_layer_isolation_hook()
    result = await hook(
        "some_writer",
        {"target_file": "calibration/CALIBRATION_LOCKED"},
        {},
    )
    assert _is_deny(result)


# ---------------------------------------------------------------------------
# 3. synthesize_baseline writing to baselines/data/ passes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hook_allows_synthesize_baseline_writing_to_baselines():
    hook = make_layer_isolation_hook()
    result = await hook(
        "synthesize_baseline",
        {"target_file": "baselines/data/family_scale.yaml",
         "target_path": "dap_billion.value"},
        {},
    )
    assert not _is_deny(result), (
        "synthesize_baseline writing under baselines/data/ must be allowed"
    )


# ---------------------------------------------------------------------------
# 4. synthesize_baseline cannot escape its scope (PRINCIPAL FOCUS)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hook_blocks_synthesize_baseline_writing_to_params():
    """The hook denies even synthesize_* tools whose target escapes the
    deterministic-layer prefix.  This is the load-bearing prompt-injection
    defence — a hijacked tool call that swaps target_file to params/x.yaml
    must be denied at the SDK boundary."""
    hook = make_layer_isolation_hook()
    result = await hook(
        "synthesize_baseline",
        {"target_file": "params/segment_propensities.yaml",
         "target_path": "elasticities.dap_billion.value"},
        {},
    )
    assert _is_deny(result)
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "params/" in reason
