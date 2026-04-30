"""Tests for baselines/tools/synthesize_baseline.py — the SOLE WRITER.

Per AGENTIC_ARCHITECTURE_INDEX.md §2.4 / §8 and architecture_research.pdf
§10.3, synthesize_baseline is the only tool allowed to write canonical
baselines/data/*.yaml.  These tests pin the load-bearing invariants:

  * NOT readOnlyHint (it writes)
  * target_file restricted to baselines/data/
  * Opus 4.7 + thinking + strict tool use for the judge call
  * accept + confidence ≥ 0.85 → atomic .tmp + os.rename
  * reject OR confidence < 0.85 → no write
  * Calibration drift → CalibrationLockError BEFORE ctx.sample
  * Persistence (observations + syntheses) is invoked regardless
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

import baselines.tools.synthesize_baseline as syn_module
from baselines.schemas import (
    ExtractedMetric,
    ExtractedMetricSource,
    SanitizerFlag,
    SynthesizerDecision,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_calibration(monkeypatch):
    """Force the calibration lock to look valid + non-drifting for the bulk
    of the suite.  Individual tests override these to exercise the drift
    + elicit branches."""
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module,
        "verify_lock_against_current_state",
        lambda **_: (True, []),
    )
    monkeypatch.setattr(
        syn_module, "would_change_locked_hash", lambda *a, **k: False
    )
    yield


@pytest.fixture
def target_file(tmp_path) -> str:
    """A baselines/data/<file>.yaml-shaped path under tmp_path.  Tests
    reference it via the relative path the synthesizer expects."""
    data_dir = tmp_path / "baselines" / "data"
    data_dir.mkdir(parents=True)
    f = data_dir / "test_metric.yaml"
    f.write_text(yaml.safe_dump({
        "dap_billion": {
            "value": 3.50,
            "source": "old_source",
            "provenance": "industry",
            "confidence": "low",
        },
    }, sort_keys=False))
    return str(f.relative_to(tmp_path))


@pytest.fixture
def chdir_tmp(tmp_path, monkeypatch):
    """All tests run inside tmp_path so target_file paths are tmp-rooted."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


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
async def test_synthesizer_registered_without_readonly_hint():
    from baselines.server import baselines_server
    tools = await baselines_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert "synthesize_baseline" in by_name
    # Five tools after M12c-ii: 3 sanitize_*, 1 extract, 1 synthesize.
    assert {"sanitize_schema", "sanitize_policy", "sanitize_source_tier",
            "extract_metric", "synthesize_baseline"} <= set(by_name)
    # synthesize_baseline writes — must NOT be readOnlyHint=True.
    annotations = by_name["synthesize_baseline"].annotations
    assert getattr(annotations, "readOnlyHint", None) is not True


# ---------------------------------------------------------------------------
# 2. Target scope restriction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_rejects_outside_target(chdir_tmp):
    ctx = _make_ctx()  # not used — should error out before the judge call
    with pytest.raises(ValueError, match="baselines/data/"):
        await syn_module.synthesize_baseline(
            extracted=_metric(),
            sanitizer_flags=[],
            target_file="params/segment_propensities.yaml",
            target_path="dap_billion",
            ctx=ctx,
        )
    assert ctx.sample.await_count == 0


# ---------------------------------------------------------------------------
# 3. Judge call: Opus + thinking + strict tool use
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_calls_judge_with_thinking_and_strict_tool(chdir_tmp, target_file):
    ctx = _make_ctx(_decision_response("accept", 0.95))
    await syn_module.synthesize_baseline(
        extracted=_metric(),
        sanitizer_flags=[],
        target_file=target_file,
        target_path="dap_billion",
        ctx=ctx,
    )
    assert ctx.sample.await_count == 1
    kwargs = ctx.sample.call_args.kwargs

    # Opus 4.7 — workers were Sonnet, the judge is Opus (PDF §7.4).
    model = kwargs.get("model", "")
    assert "opus" in model.lower(), f"expected Opus for synthesizer judge, got {model!r}"

    # Extended thinking enabled with the prescribed budget.
    thinking = kwargs.get("thinking") or {}
    assert thinking.get("type") == "enabled"
    assert thinking.get("budget_tokens", 0) >= 16000

    # Strict tool use, locked to emit_decision.
    tools = kwargs.get("tools") or []
    assert tools and tools[0]["name"] == "emit_decision"
    assert tools[0]["strict"] is True
    assert kwargs.get("tool_choice") == {"type": "tool", "name": "emit_decision"}

    # System prompt is cached at ttl=1h (PDF §6.1).
    system = kwargs.get("system")
    assert isinstance(system, list) and system
    cache = system[0].get("cache_control", {})
    assert cache.get("type") == "ephemeral"
    assert cache.get("ttl") == "1h"


# ---------------------------------------------------------------------------
# 4. Accept + confidence ≥ floor → write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_writes_on_accept(chdir_tmp, target_file):
    ctx = _make_ctx(_decision_response("accept", 0.95))
    decision = await syn_module.synthesize_baseline(
        extracted=_metric(value=3.58),
        sanitizer_flags=[],
        target_file=target_file,
        target_path="dap_billion",
        ctx=ctx,
    )
    assert isinstance(decision, SynthesizerDecision)
    assert decision.decision == "accept"

    written = yaml.safe_load(Path(target_file).read_text())
    assert written["dap_billion"]["value"] == 3.58
    # Sibling fields preserved on update.
    assert "provenance" in written["dap_billion"]


# ---------------------------------------------------------------------------
# 5. Reject → no write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_does_not_write_on_reject(chdir_tmp, target_file):
    before = Path(target_file).read_text()
    ctx = _make_ctx(_decision_response("reject", 0.0, rationale="bad source"))
    decision = await syn_module.synthesize_baseline(
        extracted=_metric(value=99.99),
        sanitizer_flags=[],
        target_file=target_file,
        target_path="dap_billion",
        ctx=ctx,
    )
    assert decision.decision == "reject"
    after = Path(target_file).read_text()
    assert before == after, "reject must not touch the canonical file"


# ---------------------------------------------------------------------------
# 6. Below confidence floor → no write even on accept
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_does_not_write_below_confidence_floor(chdir_tmp, target_file):
    before = Path(target_file).read_text()
    ctx = _make_ctx(_decision_response("accept", 0.80))  # below 0.85 floor
    decision = await syn_module.synthesize_baseline(
        extracted=_metric(value=99.99),
        sanitizer_flags=[],
        target_file=target_file,
        target_path="dap_billion",
        ctx=ctx,
    )
    assert decision.decision == "accept"
    assert decision.confidence == pytest.approx(0.80)
    after = Path(target_file).read_text()
    assert before == after, "below-floor accept must not write"


# ---------------------------------------------------------------------------
# 7. Calibration drift aborts BEFORE the judge call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_calibration_drift_aborts(monkeypatch, chdir_tmp, target_file):
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module,
        "verify_lock_against_current_state",
        lambda **_: (False, ["params/segment_propensities.yaml"]),
    )
    ctx = _make_ctx(_decision_response("accept", 0.95))
    with pytest.raises(lock_module.CalibrationLockError):
        await syn_module.synthesize_baseline(
            extracted=_metric(),
            sanitizer_flags=[],
            target_file=target_file,
            target_path="dap_billion",
            ctx=ctx,
        )
    # Crucially: the judge call NEVER happened.
    assert ctx.sample.await_count == 0


# ---------------------------------------------------------------------------
# 8. Atomic write — go through .tmp + os.rename
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_atomic_write(monkeypatch, chdir_tmp, target_file):
    rename_calls: list[tuple[str, str]] = []
    real_rename = os.rename

    def spy_rename(src, dst):
        rename_calls.append((str(src), str(dst)))
        return real_rename(src, dst)

    monkeypatch.setattr(syn_module.os, "rename", spy_rename)

    ctx = _make_ctx(_decision_response("accept", 0.95))
    await syn_module.synthesize_baseline(
        extracted=_metric(value=3.58),
        sanitizer_flags=[],
        target_file=target_file,
        target_path="dap_billion",
        ctx=ctx,
    )

    assert len(rename_calls) == 1
    src, dst = rename_calls[0]
    assert src.endswith(".tmp"), f"expected atomic write via .tmp, got src={src!r}"
    assert dst == target_file
    # The .tmp file must NOT be left behind after the rename.
    assert not Path(src).exists()


# ---------------------------------------------------------------------------
# 9. Persistence helpers are invoked regardless of decision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_persists_observation_and_synthesis(monkeypatch, chdir_tmp, target_file):
    persist_synth = AsyncMock()
    persist_obs = AsyncMock()
    monkeypatch.setattr(syn_module, "_persist_synthesis_decision", persist_synth)
    monkeypatch.setattr(syn_module, "_persist_observation_flags", persist_obs)

    flags = [
        SanitizerFlag(
            kind="policy",
            severity="medium",
            evidence=["aggregator URL"],
            suggested_fix="prefer primary",
        )
    ]
    ctx = _make_ctx(_decision_response("accept", 0.95))
    await syn_module.synthesize_baseline(
        extracted=_metric(),
        sanitizer_flags=flags,
        target_file=target_file,
        target_path="dap_billion",
        ctx=ctx,
    )
    assert persist_synth.await_count == 1
    assert persist_obs.await_count == 1
    # Both calls received the SynthesizerDecision / flags / target context.
    synth_args = persist_synth.await_args.args
    assert isinstance(synth_args[0], SynthesizerDecision)
    obs_args = persist_obs.await_args.args
    assert obs_args[0] == flags
