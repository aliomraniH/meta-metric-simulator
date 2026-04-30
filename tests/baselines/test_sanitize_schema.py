"""Tests for baselines/tools/sanitize_schema.py.

The tool is async and returns list[SanitizerFlag].  Tests cover the
clean-pass case + each violation rule + a multiple-violation case.
"""
from __future__ import annotations

import pytest

from baselines.schemas import ExtractedMetric, ExtractedMetricSource, SanitizerFlag
from baselines.tools.sanitize_schema import sanitize_schema


def _src(
    *,
    doc_title: str = "Meta Q4 2025 press release",
    page: int | None = 3,
    quoted_text: str = "DAP of 3.58 billion, +7% YoY",
    url: str = "https://investor.atmeta.com/x",
) -> ExtractedMetricSource:
    return ExtractedMetricSource(
        doc_title=doc_title, page=page,
        quoted_text=quoted_text, url=url,
    )


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


async def _run(extracted: ExtractedMetric) -> list[SanitizerFlag]:
    """sanitize_schema is registered via @baselines_server.tool but
    fastmcp 3.x returns the underlying function unchanged, so we call
    it directly here without standing up a live MCP context."""
    return await sanitize_schema(extracted)


# -----------------------------------------------------------------------------
# Clean pass
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clean_metric_returns_no_flags():
    flags = await _run(_metric())
    assert flags == []


# -----------------------------------------------------------------------------
# Bound checks per unit
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pct_above_100_flagged_high():
    flags = await _run(_metric(unit="pct", value=150.0))
    bound = [f for f in flags if f.kind == "bound"]
    assert len(bound) == 1
    assert bound[0].severity == "high"
    assert "above ceiling" in bound[0].evidence[0]


@pytest.mark.asyncio
async def test_pct_negative_flagged_high():
    flags = await _run(_metric(unit="pct", value=-1.0))
    bound = [f for f in flags if f.kind == "bound"]
    assert len(bound) == 1
    assert bound[0].severity == "high"
    assert "below floor" in bound[0].evidence[0]


@pytest.mark.asyncio
async def test_negative_usd_billion_flagged_critical():
    flags = await _run(_metric(unit="usd_billion", value=-5.0))
    bound = [f for f in flags if f.kind == "bound"]
    assert len(bound) == 1
    assert bound[0].severity == "critical"


@pytest.mark.asyncio
async def test_ratio_above_10_flagged_high():
    flags = await _run(_metric(unit="ratio", value=42.0))
    bound = [f for f in flags if f.kind == "bound"]
    assert len(bound) == 1
    assert bound[0].severity == "high"


# -----------------------------------------------------------------------------
# Provenance presence
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_provenance_flagged_critical():
    flags = await _run(_metric(source=_src(doc_title="", url="")))
    prov = [f for f in flags if f.kind == "provenance" and f.severity == "critical"]
    assert len(prov) == 1


@pytest.mark.asyncio
async def test_url_only_provenance_passes():
    """url alone satisfies the presence check."""
    flags = await _run(_metric(source=_src(doc_title="", url="https://x.com/y")))
    critical_prov = [
        f for f in flags
        if f.kind == "provenance" and f.severity == "critical"
    ]
    assert critical_prov == []


@pytest.mark.asyncio
async def test_doc_title_only_provenance_passes():
    """doc_title alone satisfies the presence check."""
    flags = await _run(_metric(source=_src(doc_title="Meta Q4 2025 8-K", url="")))
    critical_prov = [
        f for f in flags
        if f.kind == "provenance" and f.severity == "critical"
    ]
    assert critical_prov == []


@pytest.mark.asyncio
async def test_short_quoted_text_flagged_medium():
    flags = await _run(_metric(source=_src(quoted_text="ok")))  # 2 chars
    medium_prov = [
        f for f in flags
        if f.kind == "provenance" and f.severity == "medium"
    ]
    assert len(medium_prov) == 1
    assert "below minimum" in medium_prov[0].evidence[0]


# -----------------------------------------------------------------------------
# Multiple violations aggregate
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_violations_returns_all_flags():
    """A pct value out of bounds AND missing provenance produces both flags."""
    flags = await _run(_metric(
        unit="pct",
        value=200.0,
        source=_src(doc_title="", url="", quoted_text=""),
    ))
    kinds = {f.kind for f in flags}
    severities = {f.severity for f in flags}
    assert "bound" in kinds
    assert "provenance" in kinds
    assert "critical" in severities  # provenance critical
    assert "high" in severities      # bound high
    # The empty quoted_text triggers the medium provenance flag too,
    # but the critical one supersedes for severity-blocking purposes.
    assert len([f for f in flags if f.kind == "provenance"]) >= 1


@pytest.mark.asyncio
async def test_readonly_hint_annotated():
    """The layer-isolation hook reads readOnlyHint to know this tool is
    not a writer.  FastMCP stores annotations on the registered Tool
    object; we look it up via the server's list_tools()."""
    from baselines.server import baselines_server

    tools = await baselines_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert "sanitize_schema" in by_name, f"missing tool; have {sorted(by_name)}"
    assert by_name["sanitize_schema"].annotations.readOnlyHint is True
