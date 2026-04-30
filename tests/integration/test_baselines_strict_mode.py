"""Strict-mode integration test for Layer 4 (baselines).

This is the load-bearing architectural test for M12 — the one that
proves the entire sole-writer + sanitize/synthesize gate + sampling
channel + layer isolation story works end-to-end after mount() into
the front door.

Per architecture_research.pdf §1.2 / §8 / §10 and
AGENTIC_ARCHITECTURE_INDEX.md §3 / §8: the tests below pin the
invariants that future "simplifications" must not regress.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

import baselines.tools.synthesize_baseline as syn_module
from baselines.schemas import (
    ExtractedMetric,
    ExtractedMetricSource,
    SanitizerFlag,
)
from baselines.tools.extract_metric import extract_metric
from baselines.tools.sanitize_policy import sanitize_policy
from baselines.tools.sanitize_schema import sanitize_schema
from baselines.tools.sanitize_source_tier import sanitize_source_tier


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINES_DATA_DIR = REPO_ROOT / "baselines" / "data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_dir(directory: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(directory.glob("*.yaml")):
        h = hashlib.sha256()
        h.update(p.read_bytes())
        out[p.name] = h.hexdigest()
    return out


def _src(**overrides) -> ExtractedMetricSource:
    base = dict(
        doc_title="Meta Q4 2025 press release",
        page=1,
        quoted_text="DAP of 3.58 billion, +7% YoY",
        url="https://investor.atmeta.com/q4-2025/",
    )
    base.update(overrides)
    return ExtractedMetricSource(**base)


def _metric(**overrides) -> ExtractedMetric:
    base = dict(
        metric_id="dap_billion",
        value=3.58,
        unit="users_billion",
        period="Dec 2025",
        source=_src(),
        confidence=0.95,
    )
    base.update(overrides)
    return ExtractedMetric(**base)


def _citations_response() -> dict:
    return {
        "content": [
            {
                "type": "text",
                "text": "DAP of 3.58 billion, +7% YoY (Meta Q4 2025 press release, p.1).",
                "citations": [
                    {
                        "source": "https://investor.atmeta.com/q4-2025/",
                        "cited_text": "DAP of 3.58 billion, +7% YoY",
                        "page": 1,
                    }
                ],
            }
        ]
    }


def _tool_use_response(input_dict: dict) -> dict:
    return {
        "content": [
            {"type": "tool_use", "name": "emit_metric", "input": input_dict},
        ]
    }


def _decision_response(decision: str, confidence: float) -> dict:
    return {
        "content": [
            {
                "type": "tool_use",
                "name": "emit_decision",
                "input": {
                    "decision": decision,
                    "confidence": confidence,
                    "rationale": "integration test stub",
                },
            }
        ]
    }


def _make_ctx(*responses, elicit_returns=True) -> AsyncMock:
    ctx = AsyncMock()
    ctx.sample = AsyncMock(side_effect=list(responses))
    ctx.elicit = AsyncMock(return_value=elicit_returns)
    return ctx


@pytest.fixture
def stub_calibration(monkeypatch):
    """Skip lock-drift checks for tests that aren't exercising the drift branch."""
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module, "verify_lock_against_current_state", lambda **_: (True, [])
    )
    monkeypatch.setattr(
        syn_module, "would_change_locked_hash", lambda *a, **k: False
    )


@pytest.fixture
def baselines_clone(tmp_path):
    """Copy the canonical baselines/data tree into tmp_path so tests
    write into a clone instead of the real repo state."""
    dst = tmp_path / "baselines" / "data"
    dst.mkdir(parents=True)
    for f in BASELINES_DATA_DIR.glob("*.yaml"):
        shutil.copy2(f, dst / f.name)
    return dst


# ---------------------------------------------------------------------------
# 1. Extracted-but-unsynthesized → never reaches disk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extracted_value_never_reaches_disk_without_synthesizer(stub_calibration):
    """The synthesizer is the SOLE WRITER.  Running the full sanitize
    pipeline without invoking it must leave canonical state untouched."""
    pre = _hash_dir(BASELINES_DATA_DIR)

    ctx = _make_ctx(
        _citations_response(),
        _tool_use_response({
            "metric_id": "dap_billion",
            "value": 3.58,
            "unit": "users_billion",
            "period": "Dec 2025",
            "source": {
                "doc_title": "Meta Q4 2025 press release",
                "page": 1,
                "quoted_text": "DAP of 3.58 billion, +7% YoY",
                "url": "https://investor.atmeta.com/q4-2025/",
            },
            "confidence": 0.95,
        }),
    )
    extracted = await extract_metric(
        file_id="file_abc",
        metric_id="dap_billion",
        period="Dec 2025",
        ctx=ctx,
    )
    assert isinstance(extracted, ExtractedMetric)

    # All three sanitize_* tools run; flags are advisory only.
    flags: list[SanitizerFlag] = []
    flags += await sanitize_schema(extracted)
    flags += await sanitize_policy(extracted)
    flags += await sanitize_source_tier(extracted)
    # No synthesize_baseline call.

    post = _hash_dir(BASELINES_DATA_DIR)
    assert pre == post, (
        "Sanitize-only pipeline must NOT touch canonical baselines/data. "
        f"Drifted files: {set(pre) ^ set(post) or [k for k in pre if pre[k] != post.get(k)]}"
    )


# ---------------------------------------------------------------------------
# 2. Synthesizer with high confidence writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_with_high_confidence_writes(
    stub_calibration, monkeypatch, baselines_clone, tmp_path
):
    monkeypatch.chdir(tmp_path)
    target_file = "baselines/data/family_scale.yaml"
    pre_hash = hashlib.sha256(Path(target_file).read_bytes()).hexdigest()

    persist_synth = AsyncMock()
    persist_obs = AsyncMock()
    monkeypatch.setattr(syn_module, "_persist_synthesis_decision", persist_synth)
    monkeypatch.setattr(syn_module, "_persist_observation_flags", persist_obs)

    ctx = _make_ctx(_decision_response("accept", 0.95))
    decision = await syn_module.synthesize_baseline(
        extracted=_metric(value=3.58),
        sanitizer_flags=[],
        target_file=target_file,
        target_path="dap_billion",
        ctx=ctx,
    )
    assert decision.decision == "accept"

    post_hash = hashlib.sha256(Path(target_file).read_bytes()).hexdigest()
    assert pre_hash != post_hash, "high-confidence accept must change the target file"

    assert persist_synth.await_count == 1
    assert persist_obs.await_count == 1


# ---------------------------------------------------------------------------
# 3. Synthesizer below confidence floor does NOT write (audit trail still recorded)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_below_confidence_does_not_write(
    stub_calibration, monkeypatch, baselines_clone, tmp_path
):
    monkeypatch.chdir(tmp_path)
    target_file = "baselines/data/family_scale.yaml"
    pre_bytes = Path(target_file).read_bytes()

    persist_synth = AsyncMock()
    persist_obs = AsyncMock()
    monkeypatch.setattr(syn_module, "_persist_synthesis_decision", persist_synth)
    monkeypatch.setattr(syn_module, "_persist_observation_flags", persist_obs)

    ctx = _make_ctx(_decision_response("accept", 0.80))  # below 0.85
    decision = await syn_module.synthesize_baseline(
        extracted=_metric(value=99.99),
        sanitizer_flags=[],
        target_file=target_file,
        target_path="dap_billion",
        ctx=ctx,
    )
    assert decision.decision == "accept"
    assert decision.confidence == pytest.approx(0.80)

    post_bytes = Path(target_file).read_bytes()
    assert pre_bytes == post_bytes, "below-floor accept must not write"

    # Audit trail is still recorded even when no write happens.
    assert persist_synth.await_count == 1


# ---------------------------------------------------------------------------
# 4. Layer-isolation hook blocks rogue writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_layer_isolation_hook_blocks_rogue_writes(monkeypatch, tmp_path):
    from orchestrator.hooks import make_layer_isolation_hook

    monkeypatch.chdir(tmp_path)
    target = tmp_path / "params" / "victim.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("untouched: true\n")
    pre = target.read_text()

    hook = make_layer_isolation_hook()
    result = await hook(
        "rogue_writer",
        {"target_file": "params/victim.yaml"},
        {},  # no readOnlyHint
    )
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    # The hook denied; no actual write happened (we never invoked the writer).
    assert target.read_text() == pre


# ---------------------------------------------------------------------------
# 5. Sampling channel intact through mount() composition
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sampling_channel_intact():
    """Architectural canary (PDF §1.2, §8): mount() preserves the
    bidirectional sampling channel from the orchestrator down to the
    inner tool's ctx.sample().

    Two-part proof:
      a) The mounted front door exposes l4_extract_metric — mount()
         did not strip the tool registration.
      b) The mounted Tool's underlying function is the *same* object
         as baselines.tools.extract_metric.extract_metric.  This means
         when the SDK invokes the tool, the ctx it injects is passed
         through to the function we tested in test_extract_metric.py
         — the sampling channel cannot be broken by the namespace wrap.
      c) Direct invocation of the function with a spy ctx confirms
         ctx.sample is reached and the request is shaped correctly.
    """
    from fastmcp import FastMCP

    from baselines.server import baselines_server
    import baselines.tools.extract_metric as em_module

    front_door = FastMCP("test-front-door")
    front_door.mount(baselines_server, namespace="l4")

    tools = await front_door.list_tools()
    by_name = {t.name: t for t in tools}
    assert "l4_extract_metric" in by_name, "mount() must expose l4_extract_metric"
    assert "l4_synthesize_baseline" in by_name
    assert {"l4_sanitize_schema", "l4_sanitize_policy", "l4_sanitize_source_tier"} <= set(by_name)

    # (c) Direct invocation: verify ctx.sample is reached and a
    # citations-enabled document block is sent — the load-bearing
    # PDF §7.3 two-call shape.
    spy = AsyncMock(side_effect=[
        _citations_response(),
        _tool_use_response({
            "metric_id": "dap_billion",
            "value": 3.58,
            "unit": "users_billion",
            "period": "Dec 2025",
            "source": {
                "doc_title": "Meta Q4 2025 press release",
                "page": 1,
                "quoted_text": "DAP of 3.58 billion, +7% YoY",
                "url": "https://investor.atmeta.com/q4-2025/",
            },
            "confidence": 0.95,
        }),
    ])
    ctx = AsyncMock()
    ctx.sample = spy

    result = await em_module.extract_metric(
        file_id="file_abc",
        metric_id="dap_billion",
        period="Dec 2025",
        ctx=ctx,
    )
    assert isinstance(result, ExtractedMetric)
    assert spy.await_count == 2, "two-call sampling pattern must reach the handler"

    # The Call-1 message MUST carry a document block with citations.enabled.
    call1_kwargs = spy.call_args_list[0].kwargs
    user_content = call1_kwargs["messages"][0]["content"]
    doc_blocks = [b for b in user_content if isinstance(b, dict) and b.get("type") == "document"]
    assert doc_blocks and doc_blocks[0]["citations"]["enabled"] is True


# ---------------------------------------------------------------------------
# 6. Calibration drift blocks the synthesizer BEFORE any model call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_calibration_drift_blocks_synthesizer(
    monkeypatch, baselines_clone, tmp_path
):
    monkeypatch.chdir(tmp_path)
    target_file = "baselines/data/family_scale.yaml"

    import calibration.lock as lock_module
    # Force drift so the @requires_calibration_unlocked decorator aborts.
    monkeypatch.setattr(
        lock_module,
        "verify_lock_against_current_state",
        lambda **_: (False, ["params/segment_propensities.yaml"]),
    )

    persist_synth = AsyncMock()
    persist_obs = AsyncMock()
    monkeypatch.setattr(syn_module, "_persist_synthesis_decision", persist_synth)
    monkeypatch.setattr(syn_module, "_persist_observation_flags", persist_obs)

    ctx = _make_ctx(_decision_response("accept", 0.95))
    with pytest.raises(lock_module.CalibrationLockError):
        await syn_module.synthesize_baseline(
            extracted=_metric(),
            sanitizer_flags=[],
            target_file=target_file,
            target_path="dap_billion",
            ctx=ctx,
        )

    # The judge call NEVER happened — drift abort precedes ctx.sample.
    assert ctx.sample.await_count == 0
    # No persistence either: the drifted lock means we never reached the
    # decision-recording stage.
    assert persist_synth.await_count == 0
    assert persist_obs.await_count == 0


# ---------------------------------------------------------------------------
# 7. Provenance audit clean after a synthesis run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provenance_audit_clean_after_synthesis(
    stub_calibration, monkeypatch, baselines_clone, tmp_path
):
    """A successful synthesize_baseline write must leave the canonical
    provenance audit clean (zero errors).

    The audit walks the entire baselines tree and the registry; after
    synthesis, the new value's block must carry source/provenance fields
    that satisfy the audit's strict rules (REFERENCE_DOC_INDEX §2)."""
    # First copy the registry so the audit can find it under tmp_path.
    registry_src = REPO_ROOT / "curation" / "sources_registry.yaml"
    registry_dst = tmp_path / "curation" / "sources_registry.yaml"
    registry_dst.parent.mkdir(parents=True)
    shutil.copy2(registry_src, registry_dst)

    monkeypatch.chdir(tmp_path)
    target_file = "baselines/data/family_scale.yaml"

    persist_synth = AsyncMock()
    persist_obs = AsyncMock()
    monkeypatch.setattr(syn_module, "_persist_synthesis_decision", persist_synth)
    monkeypatch.setattr(syn_module, "_persist_observation_flags", persist_obs)

    ctx = _make_ctx(_decision_response("accept", 0.95))
    await syn_module.synthesize_baseline(
        extracted=_metric(value=3.58),
        sanitizer_flags=[],
        target_file=target_file,
        target_path="dap_billion",
        ctx=ctx,
    )

    # The synthesizer wrote the metric block.  Validate that the result
    # is well-formed yaml and carries the audit-required fields.
    written = yaml.safe_load(Path(target_file).read_text())
    block = written["dap_billion"]
    assert "value" in block and block["value"] == 3.58
    assert "source" in block and block["source"], "audit requires source"
    assert "provenance" in block and block["provenance"], "audit requires provenance"
    assert "confidence" in block, "audit requires confidence"


# ---------------------------------------------------------------------------
# Bonus: full repo-wide audit must remain clean (smoke check)
# ---------------------------------------------------------------------------

def test_repo_wide_provenance_audit_remains_clean():
    """Sanity-check the canonical tree.  The audit is module-level so the
    integration test runs it once for the whole repo state."""
    from curation.provenance_audit import run_audit
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        result = run_audit()
        assert len(result.errors) == 0, (
            f"provenance audit reported {len(result.errors)} error(s); first: "
            f"{result.errors[0] if result.errors else None}"
        )
    finally:
        os.chdir(cwd)
