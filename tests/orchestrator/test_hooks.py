"""Unit tests for the sanitize / synthesize deliberation hooks.

These tests pin two load-bearing behaviours from `docs/AGENT_DELIBERATION.md`:

  1. PreToolUse hook DENIES a synthesizer write when confidence is below the
     contract's floor, when sanitizer severity is at or above the block
     threshold, or when required strict-mode fields are missing.
  2. PostToolUse hook REWRITES `updatedMCPToolOutput` with
     `{disputed, confidence, disputed_by, dispute_reasons}` when sanitizer
     flags are present or confidence is below the floor.

The hooks are mode-agnostic at the call site — they read the per-layer
DeliberationContract.  The tests exercise both strict (l4) and soft (l8)
contracts.
"""
from __future__ import annotations

import pytest

from orchestrator.deliberation import CONTRACTS, DeliberationContract, get
from orchestrator.hooks import (
    make_sanitize_pretooluse_hook,
    make_synthesize_posttooluse_hook,
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _pre_input(tool_name: str, **tool_input) -> dict:
    return {"tool_name": tool_name, "tool_input": tool_input}


def _post_input(tool_name: str, **tool_response) -> dict:
    return {"tool_name": tool_name, "tool_response": tool_response}


def _is_deny(result: dict) -> bool:
    spec = result.get("hookSpecificOutput", {})
    return spec.get("permissionDecision") == "deny"


def _rewritten(result: dict) -> dict:
    spec = result.get("hookSpecificOutput", {})
    return spec.get("updatedMCPToolOutput", {})


# -----------------------------------------------------------------------------
# PreToolUse — strict-mode (l4) denials
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pretooluse_denies_when_confidence_below_floor():
    contract = get("l4")  # strict, floor=0.85
    hook = make_sanitize_pretooluse_hook(contract)

    result = await hook(
        _pre_input(
            "l4_synthesize_baseline",
            synthesizer_confidence=0.80,
            sanitizer_flags=[],
            citations=[{"url": "https://sec.gov/x"}],
        ),
        tool_use_id="t1",
        context=None,
    )

    assert _is_deny(result)
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "below floor" in reason


@pytest.mark.asyncio
async def test_pretooluse_denies_when_sanitizer_severity_at_threshold():
    contract = get("l4")  # block_threshold = "high"
    hook = make_sanitize_pretooluse_hook(contract)

    result = await hook(
        _pre_input(
            "l4_synthesize_baseline",
            synthesizer_confidence=0.95,
            sanitizer_flags=[
                {"kind": "schema", "severity": "low"},
                {"kind": "policy", "severity": "high", "evidence": "uncited number"},
            ],
            citations=[{"url": "https://sec.gov/x"}],
        ),
        tool_use_id="t2",
        context=None,
    )

    assert _is_deny(result)
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "severity" in reason


@pytest.mark.asyncio
async def test_pretooluse_denies_when_required_fields_missing_in_strict_mode():
    contract = get("l5")  # strict
    hook = make_sanitize_pretooluse_hook(contract)

    # Missing synthesizer_confidence entirely.
    result = await hook(
        _pre_input("l5_synthesize_diff", citations=[{"url": "x"}]),
        tool_use_id="t3",
        context=None,
    )
    assert _is_deny(result)
    assert "synthesizer_confidence" in result["hookSpecificOutput"][
        "permissionDecisionReason"
    ]

    # Confidence present but no citations.
    result = await hook(
        _pre_input(
            "l5_synthesize_diff",
            synthesizer_confidence=0.95,
            citations=[],
        ),
        tool_use_id="t4",
        context=None,
    )
    assert _is_deny(result)
    assert "citations" in result["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.asyncio
async def test_pretooluse_allows_clean_strict_call():
    contract = get("l4")
    hook = make_sanitize_pretooluse_hook(contract)

    result = await hook(
        _pre_input(
            "l4_synthesize_baseline",
            synthesizer_confidence=0.90,
            sanitizer_flags=[{"kind": "schema", "severity": "low"}],
            citations=[{"url": "https://sec.gov/x", "page": 12}],
        ),
        tool_use_id="t5",
        context=None,
    )
    assert result == {}  # no hookSpecificOutput == allow


@pytest.mark.asyncio
async def test_pretooluse_passes_through_non_writer_tools():
    """Sanitizer / extractor tools are read-only — never gated by the hook."""
    contract = get("l4")
    hook = make_sanitize_pretooluse_hook(contract)

    # No confidence, no citations — but tool is not a synthesizer.
    result = await hook(
        _pre_input("l4_extract_metric", file_id="f-123"),
        tool_use_id="t6",
        context=None,
    )
    assert result == {}


# -----------------------------------------------------------------------------
# PreToolUse — soft mode tolerates more
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pretooluse_soft_mode_does_not_block_below_floor():
    contract = get("l8")  # soft, floor=0.60, block_threshold=critical
    hook = make_sanitize_pretooluse_hook(contract)

    result = await hook(
        _pre_input(
            "l8_synthesize_insight",
            synthesizer_confidence=0.55,  # below floor
            sanitizer_flags=[{"kind": "x", "severity": "high"}],
            citations=[],
        ),
        tool_use_id="t7",
        context=None,
    )
    # Soft mode: PreToolUse does not deny; PostToolUse handles annotation.
    assert result == {}


@pytest.mark.asyncio
async def test_pretooluse_soft_mode_blocks_only_critical():
    contract = get("l8")
    hook = make_sanitize_pretooluse_hook(contract)

    result = await hook(
        _pre_input(
            "l8_synthesize_insight",
            synthesizer_confidence=0.80,
            sanitizer_flags=[{"kind": "x", "severity": "critical"}],
            citations=[],
        ),
        tool_use_id="t8",
        context=None,
    )
    assert _is_deny(result)


# -----------------------------------------------------------------------------
# PostToolUse — soft-mode annotation
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_posttooluse_marks_disputed_when_high_severity_flag_present():
    contract = get("l8")
    hook = make_synthesize_posttooluse_hook(contract)

    result = await hook(
        _post_input(
            "l8_synthesize_insight",
            result={"narrative": "..."},
            sanitizer_flags=[{"kind": "policy", "severity": "high"}],
            synthesizer_confidence=0.75,
            synthesizer_id="insight-narrator",
        ),
        tool_use_id="t9",
        context=None,
    )

    rewritten = _rewritten(result)
    assert rewritten["disputed"] is True
    assert rewritten["confidence"] == 0.75
    assert rewritten["disputed_by"] == "insight-narrator"
    assert any("max_severity=high" in r for r in rewritten["dispute_reasons"])


@pytest.mark.asyncio
async def test_posttooluse_marks_disputed_when_confidence_below_floor():
    contract = get("l6")  # floor=0.70
    hook = make_synthesize_posttooluse_hook(contract)

    result = await hook(
        _post_input(
            "l6_synthesize_scenario",
            result={"manifest": {}},
            sanitizer_flags=[],
            synthesizer_confidence=0.65,
            synthesizer_id="scenario-compiler",
        ),
        tool_use_id="t10",
        context=None,
    )

    rewritten = _rewritten(result)
    assert rewritten["disputed"] is True
    assert any("confidence" in r for r in rewritten["dispute_reasons"])


@pytest.mark.asyncio
async def test_posttooluse_marks_disputed_on_evaluator_exhaustion():
    contract = get("l6")
    hook = make_synthesize_posttooluse_hook(contract)

    result = await hook(
        _post_input(
            "l6_synthesize_scenario",
            result={"manifest": {}},
            sanitizer_flags=[{"kind": "evaluator_exhausted", "severity": "low"}],
            synthesizer_confidence=0.85,
            synthesizer_id="scenario-compiler",
        ),
        tool_use_id="t11",
        context=None,
    )

    rewritten = _rewritten(result)
    assert rewritten["disputed"] is True
    assert "evaluator_optimizer_exhausted" in rewritten["dispute_reasons"]


@pytest.mark.asyncio
async def test_posttooluse_clean_pass_leaves_disputed_false():
    contract = get("l8")
    hook = make_synthesize_posttooluse_hook(contract)

    result = await hook(
        _post_input(
            "l8_synthesize_insight",
            result={"narrative": "..."},
            sanitizer_flags=[{"kind": "schema", "severity": "info"}],
            synthesizer_confidence=0.90,
            synthesizer_id="insight-narrator",
        ),
        tool_use_id="t12",
        context=None,
    )
    rewritten = _rewritten(result)
    assert rewritten["disputed"] is False
    assert rewritten["disputed_by"] is None
    assert rewritten["dispute_reasons"] == []


@pytest.mark.asyncio
async def test_posttooluse_skips_non_writer_tools():
    contract = get("l8")
    hook = make_synthesize_posttooluse_hook(contract)

    result = await hook(
        _post_input(
            "l8_find_similar_scenarios",
            result=[{"scenario_id": "s1"}],
        ),
        tool_use_id="t13",
        context=None,
    )
    assert result == {}


# -----------------------------------------------------------------------------
# Contract registry sanity
# -----------------------------------------------------------------------------

def test_contracts_registered_for_all_agentic_layers():
    assert set(CONTRACTS.keys()) == {"l4", "l5", "l6", "l8"}
    for layer in ("l4", "l5"):
        assert CONTRACTS[layer].mode == "strict"
        assert CONTRACTS[layer].confidence_floor == 0.85
    for layer in ("l6", "l8"):
        assert CONTRACTS[layer].mode == "soft"


def test_contracts_are_immutable():
    contract = get("l4")
    with pytest.raises((AttributeError, Exception)):
        # frozen dataclass: any attribute set should raise
        contract.confidence_floor = 0.10  # type: ignore[misc]


def test_blocks_for_confidence_only_in_strict():
    strict = DeliberationContract(layer="x", mode="strict", confidence_floor=0.85)
    soft = DeliberationContract(layer="y", mode="soft", confidence_floor=0.85)
    assert strict.blocks_for_confidence(0.50) is True
    assert soft.blocks_for_confidence(0.50) is False
