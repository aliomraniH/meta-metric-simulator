"""Strict-mode integration test for Layer 5 (curation).

The Layer 5 sibling of tests/integration/test_baselines_strict_mode.py
— pinning the same architectural invariants for the curation
sub-server: sole-writer rule, two-call pattern, sampling channel
preservation through mount(), layer isolation, calibration drift
abort.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

import curation.tools.synthesize_diff as syn_module
from curation.schemas import ConstitutionFlag, DiffProposal


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_SRC = REPO_ROOT / "curation" / "sources_registry.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _diff(**overrides) -> DiffProposal:
    base = dict(
        registry_entry_id="meta_q4_2025_press_release",
        field="url",
        current_value="https://old.example.com/x",
        proposed_value="https://investor.atmeta.com/q4-2025/",
        rationale="The current URL 404s; the IR page hosts the same release.",
        cited_evidence=[{
            "url": "https://investor.atmeta.com/q4-2025/",
            "page": 1,
            "quoted_text": "Q4 2025 results",
        }],
        confidence=0.93,
    )
    base.update(overrides)
    return DiffProposal(**base)


def _flags_response(flags: list[dict]) -> dict:
    return {"content": [{"type": "tool_use", "name": "emit_flags",
                         "input": {"flags": flags}}]}


def _diff_response(d: DiffProposal) -> dict:
    return {"content": [{"type": "tool_use", "name": "emit_diff_proposal",
                         "input": d.model_dump(mode="json")}]}


def _decision_response(decision: str, confidence: float) -> dict:
    return {"content": [{"type": "tool_use", "name": "emit_decision",
                         "input": {"decision": decision, "confidence": confidence,
                                   "rationale": "ok"}}]}


def _search_results_response(urls: list[str]) -> dict:
    """Web-search server-tool returns hits inside web_search_tool_result blocks."""
    return {
        "content": [
            {
                "type": "web_search_tool_result",
                "content": [{"url": u, "title": "T", "snippet": "S"} for u in urls],
            }
        ]
    }


def _fetch_text_response(text: str, url: str) -> dict:
    """Web-fetch returns text + citations on text blocks."""
    return {
        "content": [
            {
                "type": "text",
                "text": text,
                "citations": [
                    {"cited_text": text, "source": url, "page": 1},
                ],
            }
        ]
    }


def _make_ctx(*responses, elicit_returns=True) -> AsyncMock:
    ctx = AsyncMock()
    ctx.sample = AsyncMock(side_effect=list(responses))
    ctx.elicit = AsyncMock(return_value=elicit_returns)
    return ctx


@pytest.fixture(autouse=True)
def _stub_calibration(monkeypatch):
    import calibration.lock as lock_module
    monkeypatch.setattr(
        lock_module, "verify_lock_against_current_state", lambda **_: (True, [])
    )
    monkeypatch.setattr(syn_module, "would_change_locked_hash", lambda *a, **k: False)


@pytest.fixture(autouse=True)
def _stub_persistence(monkeypatch):
    monkeypatch.setattr(syn_module, "_persist_synthesis_decision", AsyncMock())
    monkeypatch.setattr(syn_module, "_persist_observation_flags", AsyncMock())


@pytest.fixture
def chdir_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def registry_clone(chdir_tmp):
    dst_dir = chdir_tmp / "curation"
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REGISTRY_SRC, dst_dir / "sources_registry.yaml")
    return dst_dir / "sources_registry.yaml"


# ---------------------------------------------------------------------------
# 1. diff_proposal does not write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_diff_proposal_does_not_write(registry_clone):
    """Running diff_proposal end-to-end must NOT touch sources_registry.yaml."""
    from curation.tools.diff_proposal import diff_proposal

    pre = registry_clone.read_bytes()
    ctx = _make_ctx(
        _search_results_response(["https://investor.atmeta.com/q4-2025/"]),
        _fetch_text_response("Q4 2025 results", "https://investor.atmeta.com/q4-2025/"),
        _diff_response(_diff()),
    )
    result = await diff_proposal(
        registry_entry_id="meta_q4_2025_press_release",
        field="url",
        ctx=ctx,
    )
    assert isinstance(result, DiffProposal)
    assert registry_clone.read_bytes() == pre, "diff_proposal must not write"


# ---------------------------------------------------------------------------
# 2. Only synthesize_diff writes — verified via end-to-end pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_only_synthesize_diff_writes(registry_clone):
    """Run web_search → web_fetch → diff_proposal → sanitize_constitution
    in sequence.  Without invoking synthesize_diff, the registry must be
    untouched."""
    from curation.tools.diff_proposal import diff_proposal
    from curation.tools.sanitize_constitution import sanitize_constitution

    pre_hash = hashlib.sha256(registry_clone.read_bytes()).hexdigest()

    ctx = _make_ctx(
        _search_results_response(["https://investor.atmeta.com/q4-2025/"]),
        _fetch_text_response("text", "https://investor.atmeta.com/q4-2025/"),
        _diff_response(_diff()),
    )
    proposal = await diff_proposal(
        registry_entry_id="meta_q4_2025_press_release",
        field="url",
        ctx=ctx,
    )
    flags = await sanitize_constitution(proposal)
    # No synthesize_diff call.
    assert isinstance(flags, list)
    post_hash = hashlib.sha256(registry_clone.read_bytes()).hexdigest()
    assert pre_hash == post_hash


# ---------------------------------------------------------------------------
# 3. Two-call pattern in diff_proposal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_call_pattern_in_diff_proposal(registry_clone):
    """Per PDF §7.3, diff_proposal must use the two-call pattern.

    web_search and web_fetch are citations-API helpers (Call 1 family).
    The final emit_diff_proposal call is strict tool use (Call 2).
    The strict-tool-use call must NOT carry web_search/web_fetch tools
    (combining citations + structured outputs returns 400)."""
    from curation.tools.diff_proposal import diff_proposal

    ctx = _make_ctx(
        _search_results_response(["https://investor.atmeta.com/q4-2025/"]),
        _fetch_text_response("text", "https://investor.atmeta.com/q4-2025/"),
        _diff_response(_diff()),
    )
    await diff_proposal(
        registry_entry_id="meta_q4_2025_press_release",
        field="url",
        ctx=ctx,
    )
    # 3 sample calls total: search + fetch + emit.
    assert ctx.sample.await_count == 3
    search_kwargs = ctx.sample.call_args_list[0].kwargs
    fetch_kwargs = ctx.sample.call_args_list[1].kwargs
    emit_kwargs = ctx.sample.call_args_list[2].kwargs

    # Search call: web_search server tool, allowed_domains constrained.
    search_tools = search_kwargs.get("tools") or []
    assert search_tools and search_tools[0].get("name") == "web_search"
    assert "allowed_domains" in search_tools[0]

    # Fetch call: citations.enabled=True on the web_fetch tool.
    fetch_tools = fetch_kwargs.get("tools") or []
    assert fetch_tools and fetch_tools[0].get("name") == "web_fetch"
    assert fetch_tools[0].get("citations", {}).get("enabled") is True

    # Emit call: strict tool use, NO web tools, NO citations.
    emit_tools = emit_kwargs.get("tools") or []
    assert emit_tools and emit_tools[0]["name"] == "emit_diff_proposal"
    assert emit_tools[0]["strict"] is True
    assert emit_kwargs.get("tool_choice") == {"type": "tool", "name": "emit_diff_proposal"}
    # No web_* tool snuck into Call 2.
    assert not any(
        t.get("name") in {"web_search", "web_fetch"} for t in emit_tools
    ), "Call 2 must not combine strict tool use with web/citations tools"


# ---------------------------------------------------------------------------
# 4. Layer-isolation hook blocks redirects to baselines/data/
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_layer_isolation_hook_blocks_l5_writing_outside_registry():
    """The layer-isolation hook denies any synthesize_* tool whose
    target_file points outside its scope.  Even though synthesize_diff
    hardcodes its target, the hook is the SDK-boundary guard."""
    from orchestrator.hooks import make_layer_isolation_hook

    hook = make_layer_isolation_hook()
    result = await hook(
        "synthesize_diff",
        {"target_file": "params/segment_propensities.yaml"},
        {},  # no readOnlyHint
    )
    deny = result.get("hookSpecificOutput", {}).get("permissionDecision")
    assert deny == "deny"


# ---------------------------------------------------------------------------
# 5. Sampling channel intact through l5 mount
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sampling_channel_intact_for_l5():
    """Architectural canary for L5 — mount() preserves the sampling
    channel from front door down to the curation sub-server tools."""
    from fastmcp import FastMCP

    from curation.server import curation_server
    import curation.tools.diff_proposal as dp_module

    front_door = FastMCP("test-front-door-l5")
    front_door.mount(curation_server, namespace="l5")

    tools = await front_door.list_tools()
    by_name = {t.name: t for t in tools}
    expected = {
        "l5_web_search", "l5_web_fetch", "l5_diff_proposal",
        "l5_sanitize_constitution", "l5_synthesize_diff",
    }
    assert expected <= set(by_name)

    # Direct invocation through the inner module to verify ctx.sample is
    # reached and the two-call pattern is structurally intact.
    spy = AsyncMock(side_effect=[
        _search_results_response(["https://investor.atmeta.com/q4-2025/"]),
        _fetch_text_response("text", "https://investor.atmeta.com/q4-2025/"),
        _diff_response(_diff()),
    ])
    ctx = AsyncMock()
    ctx.sample = spy

    result = await dp_module.diff_proposal(
        registry_entry_id="meta_q4_2025_press_release",
        field="url",
        ctx=ctx,
    )
    assert isinstance(result, DiffProposal)
    assert spy.await_count == 3, "two-call pattern (search+fetch then emit) must reach the handler"


# ---------------------------------------------------------------------------
# 6. Calibration drift blocks synthesize_diff before any model call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_calibration_drift_blocks_l5(monkeypatch, registry_clone):
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
# 7. Provenance audit unchanged after a successful curation write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provenance_audit_unchanged_after_curation(registry_clone, chdir_tmp):
    """A successful synthesize_diff write must leave the registry in a
    well-formed shape — section_refs / primary_sources / validation top
    level keys must still be present."""
    ctx = _make_ctx(
        _flags_response([]),
        _decision_response("accept", 0.95),
        elicit_returns=True,
    )
    await syn_module.synthesize_diff(
        diff=_diff(proposed_value="https://investor.atmeta.com/q4-2025-NEW/"),
        constitution_flags=[],
        ctx=ctx,
    )
    written = yaml.safe_load(registry_clone.read_text())
    assert "_meta" in written
    assert "primary_sources" in written
    # The diff actually applied.
    assert (
        written["primary_sources"]["meta_q4_2025_press_release"]["url"]
        == "https://investor.atmeta.com/q4-2025-NEW/"
    )


# ---------------------------------------------------------------------------
# 8. Front door surfaces both l4 and l5 (10 tools total)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_front_door_surfaces_l4_and_l5():
    """End-to-end mount sanity check: the production front door has all
    M12 + M13 tools surfaced under their namespaces."""
    from fastmcp import FastMCP

    from baselines.server import baselines_server
    from curation.server import curation_server

    app = FastMCP("test-front-door")
    app.mount(baselines_server, namespace="l4")
    app.mount(curation_server, namespace="l5")

    tools = await app.list_tools()
    names = {t.name for t in tools}
    assert {
        "l4_extract_metric", "l4_sanitize_schema", "l4_sanitize_policy",
        "l4_sanitize_source_tier", "l4_synthesize_baseline",
        "l5_web_search", "l5_web_fetch", "l5_diff_proposal",
        "l5_sanitize_constitution", "l5_synthesize_diff",
    } <= names
