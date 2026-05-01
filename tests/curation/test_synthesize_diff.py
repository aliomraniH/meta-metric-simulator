"""Tests for curation/tools/synthesize_diff.py — the SOLE WRITER to
curation/sources_registry.yaml.

These tests pin the load-bearing invariants:

  * NOT readOnlyHint (it writes)
  * Hardcoded target = curation/sources_registry.yaml — synthesize_diff
    cannot be redirected
  * Incoming high/critical flag → short-circuit reject WITHOUT model call
  * Critique-revise loop: max N=2 rounds
  * NEVER auto-apply: ctx.elicit always invoked on accept path
  * Calibration drift → CalibrationLockError BEFORE any model call
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

import curation.tools.synthesize_diff as syn_module
from curation.schemas import (
    ConstitutionFlag,
    CurationDecision,
    DiffProposal,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_SRC = REPO_ROOT / "curation" / "sources_registry.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_calibration(monkeypatch):
    """Default: lock looks valid, hashing thinks the change is non-drifting."""
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module, "verify_lock_against_current_state", lambda **_: (True, [])
    )
    monkeypatch.setattr(syn_module, "would_change_locked_hash", lambda *a, **k: False)
    yield


@pytest.fixture(autouse=True)
def _stub_persistence(monkeypatch):
    """Stub the DB persistence helpers to no-ops the test suite can spy on."""
    persist_synth = AsyncMock()
    persist_obs = AsyncMock()
    monkeypatch.setattr(syn_module, "_persist_synthesis_decision", persist_synth)
    monkeypatch.setattr(syn_module, "_persist_observation_flags", persist_obs)
    return {"persist_synth": persist_synth, "persist_obs": persist_obs}


@pytest.fixture
def chdir_tmp(tmp_path, monkeypatch):
    """Run inside tmp_path so the registry write lands in a clone."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def registry_clone(chdir_tmp):
    """Copy the canonical sources_registry.yaml into tmp_path/curation/."""
    dst_dir = chdir_tmp / "curation"
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REGISTRY_SRC, dst_dir / "sources_registry.yaml")
    return dst_dir / "sources_registry.yaml"


def _diff(**overrides) -> DiffProposal:
    base = dict(
        registry_entry_id="meta_q4_2025_press_release",
        field="url",
        current_value="https://old.example.com/x",
        proposed_value="https://investor.atmeta.com/q4-2025/",
        rationale="The current URL 404s; the new IR page hosts the same release.",
        cited_evidence=[{
            "url": "https://investor.atmeta.com/q4-2025/",
            "page": 1,
            "quoted_text": "Q4 2025 results",
        }],
        confidence=0.92,
    )
    base.update(overrides)
    return DiffProposal(**base)


def _flags_response(flags: list[dict]) -> dict:
    return {
        "content": [
            {
                "type": "tool_use",
                "name": "emit_flags",
                "input": {"flags": flags},
            }
        ]
    }


def _diff_response(d: DiffProposal) -> dict:
    return {
        "content": [
            {
                "type": "tool_use",
                "name": "emit_diff_proposal",
                "input": d.model_dump(mode="json"),
            }
        ]
    }


def _decision_response(decision: str, confidence: float, rationale: str = "ok") -> dict:
    return {
        "content": [
            {
                "type": "tool_use",
                "name": "emit_decision",
                "input": {
                    "decision": decision,
                    "confidence": confidence,
                    "rationale": rationale,
                },
            }
        ]
    }


def _make_ctx(*responses, elicit_returns=True) -> AsyncMock:
    ctx = AsyncMock()
    ctx.sample = AsyncMock(side_effect=list(responses))
    ctx.elicit = AsyncMock(return_value=elicit_returns)
    return ctx


# ---------------------------------------------------------------------------
# 1. Tool registration — NOT readOnlyHint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_registered():
    from curation.server import curation_server
    tools = await curation_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert "synthesize_diff" in by_name
    expected = {
        "web_search", "web_fetch", "diff_proposal",
        "sanitize_constitution", "synthesize_diff",
    }
    assert expected <= set(by_name)
    annotations = by_name["synthesize_diff"].annotations
    assert getattr(annotations, "readOnlyHint", None) is not True


# ---------------------------------------------------------------------------
# 2. Incoming critical flag → short-circuit reject, no model call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_rejects_on_critical_flag(chdir_tmp):
    ctx = _make_ctx()  # not used
    bad_flag = ConstitutionFlag(
        rule="forbidden_domain",
        severity="critical",
        evidence=["https://twitter.com/x"],
    )
    decision = await syn_module.synthesize_diff(
        diff=_diff(),
        constitution_flags=[bad_flag],
        ctx=ctx,
    )
    assert decision.decision == "reject"
    assert decision.confidence == 0.0
    assert ctx.sample.await_count == 0, "no model call when incoming flags are blocking"
    assert ctx.elicit.await_count == 0


# ---------------------------------------------------------------------------
# 3. Critique-revise loop — clean on first round → straight to judge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_critique_revise_clean_first_round(chdir_tmp, registry_clone):
    """Critic returns no flags on round 1 → no revision needed; proceed to
    Opus judge → user approves → write.  Verifies the happy path."""
    ctx = _make_ctx(
        _flags_response([]),                      # critic round 1: clean
        _decision_response("accept", 0.95),       # Opus judge
        elicit_returns=True,
    )
    decision = await syn_module.synthesize_diff(
        diff=_diff(),
        constitution_flags=[],
        ctx=ctx,
    )
    assert decision.decision == "accept"
    # 1 critic call + 1 judge call.
    assert ctx.sample.await_count == 2
    # ctx.elicit was called for diff-confirm.
    assert ctx.elicit.await_count == 1


# ---------------------------------------------------------------------------
# 4. Critique-revise loop — N=2 then accept
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_critique_revise_loop_n2(chdir_tmp, registry_clone):
    """Critic flags high on round 1 → refresher revises → critic clean on
    round 2 → judge → accept.  4 sample calls total (critic, revise, critic, judge)."""
    high_flag = {
        "rule": "secondary_aggregator_used",
        "severity": "high",
        "evidence": ["only emarketer URLs"],
        "suggested_fix": "add a primary citation",
    }
    revised = _diff(rationale=(
        "Revised: the IR page is the primary; emarketer is corroboration only."
    ))
    ctx = _make_ctx(
        _flags_response([high_flag]),       # round 1: critic flags high
        _diff_response(revised),            # refresher revises
        _flags_response([]),                # round 2: critic clean
        _decision_response("accept", 0.90), # Opus judge
        elicit_returns=True,
    )
    decision = await syn_module.synthesize_diff(
        diff=_diff(),
        constitution_flags=[],
        ctx=ctx,
    )
    assert decision.decision == "accept"
    assert ctx.sample.await_count == 4


# ---------------------------------------------------------------------------
# 5. Critique-revise loop — exhausted → escalate (no write)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_critique_revise_exhausted(chdir_tmp, registry_clone):
    """Critic flags high on both N=2 rounds → escalate, no judge call, no write."""
    pre = registry_clone.read_bytes()

    high_flag = {
        "rule": "primary_source_required",
        "severity": "high",
        "evidence": ["no primary source"],
    }
    ctx = _make_ctx(
        _flags_response([high_flag]),      # round 1: high
        _diff_response(_diff()),           # refresher revises (still bad)
        _flags_response([high_flag]),      # round 2: still high → escalate
    )
    decision = await syn_module.synthesize_diff(
        diff=_diff(),
        constitution_flags=[],
        ctx=ctx,
    )
    assert decision.decision == "escalate"
    # 3 calls: critic(round1) + refresher(revise) + critic(round2)
    # — no judge call, no diff-confirm elicit.
    assert ctx.sample.await_count == 3
    assert ctx.elicit.await_count == 0
    assert registry_clone.read_bytes() == pre, "escalate must not write"


# ---------------------------------------------------------------------------
# 6. ctx.elicit declined → no write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_elicit_required(chdir_tmp, registry_clone):
    """Judge accepts but user declines diff-confirm → no write."""
    pre = registry_clone.read_bytes()
    ctx = _make_ctx(
        _flags_response([]),
        _decision_response("accept", 0.95),
        elicit_returns=False,
    )
    decision = await syn_module.synthesize_diff(
        diff=_diff(),
        constitution_flags=[],
        ctx=ctx,
    )
    assert decision.decision == "reject"
    assert "User declined" in decision.rationale
    assert registry_clone.read_bytes() == pre, "declined diff must not touch the file"
    assert ctx.elicit.await_count == 1


# ---------------------------------------------------------------------------
# 7. Full happy path — write + observations + syntheses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_writes_on_accept_and_approval(
    chdir_tmp, registry_clone, _stub_persistence
):
    pre_hash = hashlib.sha256(registry_clone.read_bytes()).hexdigest()
    ctx = _make_ctx(
        _flags_response([]),
        _decision_response("accept", 0.97),
        elicit_returns=True,
    )
    decision = await syn_module.synthesize_diff(
        diff=_diff(proposed_value="https://investor.atmeta.com/q4-2025-NEW/"),
        constitution_flags=[],
        ctx=ctx,
    )
    assert decision.decision == "accept"
    assert decision.applied_diff is not None
    post_hash = hashlib.sha256(registry_clone.read_bytes()).hexdigest()
    assert pre_hash != post_hash, "accept + approval must change the registry"

    # Verify the diff actually landed in the right place.
    written = yaml.safe_load(registry_clone.read_text())
    entry = written["primary_sources"]["meta_q4_2025_press_release"]
    assert entry["url"] == "https://investor.atmeta.com/q4-2025-NEW/"

    # Persistence helpers were called (audit trail).
    assert _stub_persistence["persist_synth"].await_count >= 1
    assert _stub_persistence["persist_obs"].await_count >= 1


# ---------------------------------------------------------------------------
# 8. Calibration drift aborts BEFORE any model call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_calibration_drift_aborts(chdir_tmp, monkeypatch):
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module,
        "verify_lock_against_current_state",
        lambda **_: (False, ["params/segment_propensities.yaml"]),
    )
    ctx = _make_ctx(_flags_response([]), _decision_response("accept", 0.95))
    with pytest.raises(lock_module.CalibrationLockError):
        await syn_module.synthesize_diff(
            diff=_diff(),
            constitution_flags=[],
            ctx=ctx,
        )
    assert ctx.sample.await_count == 0
    assert ctx.elicit.await_count == 0


# ---------------------------------------------------------------------------
# 9. NEVER auto-apply — ctx.elicit must be invoked on the accept path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_does_not_auto_apply(chdir_tmp, registry_clone):
    """Constitution Rule 3: ctx.elicit must be called even when every other
    check is clean.  The structural test guards against a future "shortcut"
    that bypasses the human approval step."""
    ctx = _make_ctx(
        _flags_response([]),
        _decision_response("accept", 0.99),
        elicit_returns=True,
    )
    await syn_module.synthesize_diff(
        diff=_diff(),
        constitution_flags=[],
        ctx=ctx,
    )
    assert ctx.elicit.await_count >= 1, (
        "synthesize_diff must call ctx.elicit on every accept; "
        "auto-apply is a constitutional violation."
    )


# ---------------------------------------------------------------------------
# 10. Calibration-impact escalation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_calibration_impact_elicits(
    chdir_tmp, registry_clone, monkeypatch
):
    """When the proposed diff would invalidate calibration, an extra
    ctx.elicit asks the user to approve the calibration impact —
    distinct from the diff-confirm elicit."""
    monkeypatch.setattr(syn_module, "would_change_locked_hash", lambda *a, **k: True)
    pre = registry_clone.read_bytes()
    ctx = _make_ctx(_flags_response([]), elicit_returns=False)
    decision = await syn_module.synthesize_diff(
        diff=_diff(),
        constitution_flags=[],
        ctx=ctx,
    )
    assert decision.decision == "reject"
    assert "calibration" in decision.rationale.lower()
    assert registry_clone.read_bytes() == pre


# ---------------------------------------------------------------------------
# 11. apply_diff_to_registry — pure helper round-trip
# ---------------------------------------------------------------------------

def test_apply_diff_to_registry_pure_helper():
    content = {
        "_meta": {"layer": 5},
        "primary_sources": {
            "meta_q4_2025_press_release": {"url": "old", "trust_tier": 2},
        },
    }
    result = syn_module.apply_diff_to_registry(content, _diff(
        registry_entry_id="meta_q4_2025_press_release",
        field="url",
        proposed_value="new_url",
    ))
    assert result["primary_sources"]["meta_q4_2025_press_release"]["url"] == "new_url"
    # Sibling fields preserved.
    assert result["primary_sources"]["meta_q4_2025_press_release"]["trust_tier"] == 2
    # _meta untouched.
    assert result["_meta"] == {"layer": 5}
    # Original input not mutated (pure helper).
    assert content["primary_sources"]["meta_q4_2025_press_release"]["url"] == "old"
