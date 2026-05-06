"""Tests for insights/tools/narrate_anomalies.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from insights.deterministic import Anomaly
from insights.schemas import Narrative
from insights.tools.narrate_anomalies import narrate_anomalies


# ---------------------------------------------------------------------------
# Fake metric runtime — duck-types the slice narrate_anomalies needs
# ---------------------------------------------------------------------------

class _FakeRuntime:
    def __init__(self, series):
        self._series = series

    async def compute(self, metric_name, scenario_id, group_by=None, **_):
        return [{"dim": tick, "value": v} for tick, v in self._series]


def _stationary_then_spike():
    return [(i, 100.0 + (i % 3)) for i in range(14)] + [(14, 200.0)]


def _narrative_response(scenario_id, metric_name, hypothesis_text="The metric jumped to 200.0 on day 14, which is unusual."):
    return {
        "content": [{
            "type": "tool_use",
            "name": "emit_narrative",
            "input": {
                "scenario_id": scenario_id,
                "metric_name": metric_name,
                "hypotheses": [{
                    "anomaly_ref": f"{metric_name}:14",
                    "hypothesis_text": hypothesis_text,
                    "confidence": 0.8,
                    "cited_supporting_scenarios": [],
                }],
                "overall_confidence": 0.8,
            },
        }],
    }


def _make_ctx(*responses):
    ctx = AsyncMock()
    ctx.sample = AsyncMock(side_effect=list(responses))
    return ctx


# ---------------------------------------------------------------------------
# 1. Empty anomaly list → empty narrative, no model call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrate_returns_empty_for_no_anomalies():
    """Stationary series → no anomalies → no model call, empty narrative."""
    rt = _FakeRuntime([(i, 100.0) for i in range(20)])
    ctx = _make_ctx()  # no responses; verify ctx.sample never invoked
    n = await narrate_anomalies(
        scenario_id="scn-A",
        metric_name="rev",
        ctx=ctx,
        metric_runtime=rt,
    )
    assert isinstance(n, Narrative)
    assert n.hypotheses == []
    assert n.overall_confidence == pytest.approx(1.0)
    assert ctx.sample.await_count == 0


# ---------------------------------------------------------------------------
# 2. One anomaly → one hypothesis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrate_returns_narrative_for_one_anomaly():
    rt = _FakeRuntime(_stationary_then_spike())
    ctx = _make_ctx(_narrative_response("scn-A", "rev"))
    n = await narrate_anomalies(
        scenario_id="scn-A",
        metric_name="rev",
        ctx=ctx,
        metric_runtime=rt,
    )
    assert isinstance(n, Narrative)
    assert len(n.hypotheses) == 1
    assert n.hypotheses[0].anomaly_ref == "rev:14"


# ---------------------------------------------------------------------------
# 3. Sonnet, not Opus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrate_uses_sonnet():
    rt = _FakeRuntime(_stationary_then_spike())
    ctx = _make_ctx(_narrative_response("scn-A", "rev"))
    await narrate_anomalies(scenario_id="scn-A", metric_name="rev",
                            ctx=ctx, metric_runtime=rt)
    model = ctx.sample.call_args.kwargs.get("model", "")
    assert "sonnet" in model.lower()
    assert "opus" not in model.lower()


# ---------------------------------------------------------------------------
# 4. Strict tool use locked to emit_narrative
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrate_strict_tool_use():
    rt = _FakeRuntime(_stationary_then_spike())
    ctx = _make_ctx(_narrative_response("scn-A", "rev"))
    await narrate_anomalies(scenario_id="scn-A", metric_name="rev",
                            ctx=ctx, metric_runtime=rt)
    kwargs = ctx.sample.call_args.kwargs
    tools = kwargs.get("tools") or []
    assert tools and tools[0]["name"] == "emit_narrative"
    assert tools[0]["strict"] is True
    assert kwargs.get("tool_choice") == {"type": "tool", "name": "emit_narrative"}


# ---------------------------------------------------------------------------
# 5. Cache TTL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrate_caches_system_prompt_at_one_hour():
    rt = _FakeRuntime(_stationary_then_spike())
    ctx = _make_ctx(_narrative_response("scn-A", "rev"))
    await narrate_anomalies(scenario_id="scn-A", metric_name="rev",
                            ctx=ctx, metric_runtime=rt)
    system = ctx.sample.call_args.kwargs.get("system")
    assert isinstance(system, list) and system
    cache = system[0].get("cache_control", {})
    assert cache.get("type") == "ephemeral"
    assert cache.get("ttl") == "1h"


# ---------------------------------------------------------------------------
# 6. No disk writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrate_does_not_write_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    rt = _FakeRuntime(_stationary_then_spike())
    ctx = _make_ctx(_narrative_response("scn-A", "rev"))
    await narrate_anomalies(scenario_id="scn-A", metric_name="rev",
                            ctx=ctx, metric_runtime=rt)
    assert set(tmp_path.iterdir()) == before


# ---------------------------------------------------------------------------
# 7. Tool registration — readOnly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrate_registered_readonly():
    from insights.server import insights_server
    tools = await insights_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert "narrate_anomalies" in by_name
    assert by_name["narrate_anomalies"].annotations.readOnlyHint is True


# ---------------------------------------------------------------------------
# 8. Factory path: when metric_runtime is None and factory provided, factory wins
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrate_uses_runtime_factory_when_no_runtime():
    rt = _FakeRuntime([(i, 100.0) for i in range(20)])

    async def factory():
        return rt

    ctx = _make_ctx()  # stationary → no anomalies → no sample call
    n = await narrate_anomalies(
        scenario_id="scn-A",
        metric_name="rev",
        ctx=ctx,
        metric_runtime_factory=factory,
    )
    assert n.hypotheses == []


@pytest.mark.asyncio
async def test_narrate_raises_without_runtime_or_factory():
    ctx = _make_ctx()
    with pytest.raises(ValueError, match="metric_runtime"):
        await narrate_anomalies(scenario_id="scn-A", metric_name="rev", ctx=ctx)
