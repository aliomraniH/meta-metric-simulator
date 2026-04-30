"""Tests for baselines/tools/extract_metric.py.

The two-call architectural pattern (PDF §7.3) is the load-bearing
contract: Citations API call MUST NOT use strict tool use, and the
strict-tool-use call MUST NOT enable citations.  Combining returns 400
from the API and the extraction silently fails — the
test_extractor_two_call_pattern test pins this structurally so a
future "simplification" cannot regress it.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from baselines.schemas import ExtractedMetric
from baselines.tools.extract_metric import extract_metric


# ---------------------------------------------------------------------------
# Helpers — fake sample responses
# ---------------------------------------------------------------------------

def _citations_response() -> dict:
    """Mock Call-1 result: a text block carrying citations metadata."""
    return {
        "content": [
            {
                "type": "text",
                "text": "DAP of 3.58 billion, +7% YoY (Meta Q4 2025 press release, page 1).",
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


def _citations_response_no_citations() -> dict:
    """Mock Call-1 result with text but no citations attached."""
    return {
        "content": [
            {
                "type": "text",
                "text": "DAP grew 7% in Q4 2025.",
                "citations": [],
            }
        ]
    }


def _tool_use_response(tool_input: dict) -> dict:
    """Mock Call-2 result: a tool_use block with the formatted output."""
    return {
        "content": [
            {
                "type": "tool_use",
                "name": "emit_metric",
                "input": tool_input,
            }
        ]
    }


def _good_tool_input() -> dict:
    return {
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
    }


def _make_ctx(*responses) -> AsyncMock:
    """Build an async-mock ctx whose ctx.sample yields the given responses
    in order (one per call)."""
    ctx = AsyncMock()
    ctx.sample = AsyncMock(side_effect=list(responses))
    return ctx


# ---------------------------------------------------------------------------
# 1. Tool registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extractor_registered_with_readonly_hint():
    from baselines.server import baselines_server

    tools = await baselines_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert "extract_metric" in by_name
    assert by_name["extract_metric"].annotations.readOnlyHint is True


# ---------------------------------------------------------------------------
# 2. Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extractor_returns_extracted_metric():
    ctx = _make_ctx(_citations_response(), _tool_use_response(_good_tool_input()))
    result = await extract_metric(
        file_id="file_abc",
        metric_id="dap_billion",
        period="Dec 2025",
        ctx=ctx,
    )
    assert isinstance(result, ExtractedMetric)
    assert result.metric_id == "dap_billion"
    assert result.value == 3.58
    assert result.source.url == "https://investor.atmeta.com/q4-2025/"
    assert result.source.page == 1
    assert "3.58 billion" in result.source.quoted_text


# ---------------------------------------------------------------------------
# 3. Missing citations → ValueError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extractor_raises_on_missing_citations():
    ctx = _make_ctx(_citations_response_no_citations())
    with pytest.raises(ValueError, match="lacks citations"):
        await extract_metric(
            file_id="file_abc",
            metric_id="dap_billion",
            period="Dec 2025",
            ctx=ctx,
        )
    # Only Call 1 should have happened — we abort before Call 2.
    assert ctx.sample.await_count == 1


# ---------------------------------------------------------------------------
# 4. Two-call pattern — the load-bearing architectural test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extractor_two_call_pattern():
    """PDF §7.3: Citations + Structured Outputs cannot combine in one call.
    Verify our code respects that structurally.

    Call 1: document content block with citations.enabled=true; NO tools.
    Call 2: tools=[emit_metric] with strict=True; NO citations.enabled.
    """
    ctx = _make_ctx(_citations_response(), _tool_use_response(_good_tool_input()))
    await extract_metric(
        file_id="file_abc",
        metric_id="dap_billion",
        period="Dec 2025",
        ctx=ctx,
    )
    assert ctx.sample.await_count == 2

    call1_kwargs = ctx.sample.call_args_list[0].kwargs
    call2_kwargs = ctx.sample.call_args_list[1].kwargs

    # ---- Call 1: Citations on, tools absent -------------------------------
    call1_user_content = call1_kwargs["messages"][0]["content"]
    doc_blocks = [b for b in call1_user_content if b.get("type") == "document"]
    assert len(doc_blocks) == 1, "Call 1 must include a document content block"
    assert doc_blocks[0].get("citations", {}).get("enabled") is True, (
        "Call 1 must enable citations on the document block"
    )
    assert "tools" not in call1_kwargs or not call1_kwargs.get("tools"), (
        "Call 1 must NOT include strict tool use (PDF §7.3: 400 if combined with citations)"
    )
    assert "tool_choice" not in call1_kwargs, (
        "Call 1 must NOT include tool_choice"
    )

    # ---- Call 2: tools on, citations absent --------------------------------
    assert "tools" in call2_kwargs and call2_kwargs["tools"], (
        "Call 2 must include strict tool use"
    )
    assert call2_kwargs["tools"][0]["name"] == "emit_metric"
    assert call2_kwargs["tools"][0]["strict"] is True
    assert call2_kwargs.get("tool_choice") == {"type": "tool", "name": "emit_metric"}

    # The Call 2 user message is plain text (or a list of blocks); it must
    # NOT contain a document block with citations.enabled=true.
    call2_user_content = call2_kwargs["messages"][0]["content"]
    if isinstance(call2_user_content, list):
        doc_blocks_2 = [b for b in call2_user_content if isinstance(b, dict) and b.get("type") == "document"]
        assert not doc_blocks_2, "Call 2 must NOT carry a document block (citations forbidden alongside strict tool use)"


# ---------------------------------------------------------------------------
# 5. Model selection — Sonnet, not Opus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extractor_uses_sonnet_not_opus():
    ctx = _make_ctx(_citations_response(), _tool_use_response(_good_tool_input()))
    await extract_metric(
        file_id="file_abc",
        metric_id="dap_billion",
        period="Dec 2025",
        ctx=ctx,
    )
    for call in ctx.sample.call_args_list:
        model = call.kwargs.get("model", "")
        assert "sonnet" in model.lower(), f"Expected Sonnet, got {model!r}"
        assert "opus" not in model.lower(), f"Workers don't need Opus (PDF §7.4); got {model!r}"


# ---------------------------------------------------------------------------
# 6. No disk writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extractor_does_not_write_disk(tmp_path, monkeypatch):
    """The extractor is read-only.  Run it inside a tmp cwd and assert no
    file was created during the call."""
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())

    ctx = _make_ctx(_citations_response(), _tool_use_response(_good_tool_input()))
    await extract_metric(
        file_id="file_abc",
        metric_id="dap_billion",
        period="Dec 2025",
        ctx=ctx,
    )

    after = set(tmp_path.iterdir())
    assert before == after, f"extractor wrote files: {after - before}"


# ---------------------------------------------------------------------------
# 7. System prompt cached at ttl=1h
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extractor_system_prompt_cached_one_hour():
    """PDF §6.1: every cache_control block must explicitly set ttl='1h'."""
    ctx = _make_ctx(_citations_response(), _tool_use_response(_good_tool_input()))
    await extract_metric(
        file_id="file_abc",
        metric_id="dap_billion",
        period="Dec 2025",
        ctx=ctx,
    )
    for call in ctx.sample.call_args_list:
        system = call.kwargs.get("system")
        assert isinstance(system, list) and system, "system must be a non-empty list of blocks"
        cache = system[0].get("cache_control", {})
        assert cache.get("type") == "ephemeral"
        assert cache.get("ttl") == "1h", f"cache_control ttl must be '1h', got {cache.get('ttl')!r}"
