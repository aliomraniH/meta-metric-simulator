"""Tests for baselines/tools/sanitize_source_tier.py.

These tests use a tmp registry fixture rather than the live
curation/sources_registry.yaml so they don't depend on the M6c
placeholder URLs and can pin specific tier mappings.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

import baselines.tools.sanitize_source_tier as sst_module
from baselines.schemas import ExtractedMetric, ExtractedMetricSource, SanitizerFlag


@pytest.fixture(autouse=True)
def _tmp_registry(tmp_path, monkeypatch):
    """Build a small registry with real URLs + aliases and point the
    sanitizer at it.  autouse so every test in this file gets it."""
    registry = {
        "_meta": {"layer": 5, "owner": "curation"},
        "primary_sources": {
            "meta_8k_q4_2025": {
                "url": "https://www.sec.gov/Archives/edgar/data/0001326801/abc",
                "type": "sec_filing",
                "confidence": "high",
                "trust_tier": 1,
                "aliases": ["Meta 8-K Q4 2025", "Meta Q4 2025 8-K"],
            },
            "meta_q4_2025_press_release": {
                "url": "https://investor.atmeta.com/investor-news/abc",
                "type": "earnings",
                "confidence": "high",
                "trust_tier": 2,
                "aliases": ["Meta Q4 2025 press release", "Meta Q4 2025 earnings"],
            },
            "meta_q4_2025_earnings_call_transcript": {
                "url": "https://s21.q4cdn.com/399680738/q4-transcript.pdf",
                "type": "earnings",
                "confidence": "high",
                "trust_tier": 2,
                "aliases": ["Meta Q4 2025 earnings call transcript"],
            },
            "sensor_tower_cnbc_jan_2026": {
                "url": "https://www.cnbc.com/2026/01/20/sensor-tower.html",
                "type": "industry",
                "confidence": "medium",
                "trust_tier": 3,
                "aliases": ["Sensor Tower via CNBC Jan 2026"],
            },
            "demandsage_via_vidico": {
                "url": "https://www.demandsage.com/instagram-reel-statistics/",
                "type": "industry",
                "confidence": "low",
                "trust_tier": 4,
                "aliases": ["DemandSage / Vidico"],
            },
        },
    }
    path = tmp_path / "sources_registry.yaml"
    path.write_text(yaml.safe_dump(registry, sort_keys=False))
    sst_module._set_registry_path_for_tests(path)
    yield path
    # Restore the live registry so other tests / processes aren't poisoned.
    sst_module._set_registry_path_for_tests(sst_module._DEFAULT_REGISTRY_PATH)


def _src(**overrides) -> ExtractedMetricSource:
    base = dict(
        doc_title="Meta Q4 2025 press release",
        page=3,
        quoted_text="DAP of 3.58 billion, +7% YoY",
        url="https://investor.atmeta.com/investor-news/abc",
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


async def _run(extracted: ExtractedMetric) -> list[SanitizerFlag]:
    from baselines.tools.sanitize_source_tier import sanitize_source_tier
    return await sanitize_source_tier(extracted)


# -----------------------------------------------------------------------------
# Tier 1 / 2 — should not flag
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sec_source_tier_1_high_stakes_no_flag():
    """A tier-1 SEC source for a high-stakes metric should pass clean."""
    flags = await _run(_metric(
        metric_id="dap_billion",
        source=_src(
            doc_title="Meta 8-K Q4 2025",
            url="https://www.sec.gov/Archives/edgar/data/0001326801/abc",
        ),
    ))
    assert flags == []


@pytest.mark.asyncio
async def test_earnings_call_tier_2_high_stakes_no_flag():
    flags = await _run(_metric(
        metric_id="dap_billion",
        source=_src(
            doc_title="Meta Q4 2025 earnings call transcript",
            url="https://s21.q4cdn.com/399680738/q4-transcript.pdf",
        ),
    ))
    assert flags == []


# -----------------------------------------------------------------------------
# Tier 3 — only flagged for high-stakes
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyst_tier_3_low_stakes_no_flag():
    """A tier-3 analyst source for a low-stakes metric should pass."""
    flags = await _run(_metric(
        metric_id="reels_engagement_rate_pct",
        unit="pct",
        value=1.23,
        source=_src(
            doc_title="Sensor Tower via CNBC Jan 2026",
            url="https://www.cnbc.com/2026/01/20/sensor-tower.html",
        ),
    ))
    assert flags == []


@pytest.mark.asyncio
async def test_analyst_tier_3_high_stakes_flagged_medium():
    """Tier-3 analyst for a high-stakes calibration anchor → medium flag."""
    flags = await _run(_metric(
        metric_id="dap_billion",  # high-stakes
        source=_src(
            doc_title="Sensor Tower via CNBC Jan 2026",
            url="https://www.cnbc.com/2026/01/20/sensor-tower.html",
        ),
    ))
    assert len(flags) == 1
    assert flags[0].kind == "source_tier"
    assert flags[0].severity == "medium"


# -----------------------------------------------------------------------------
# Tier 4 — high-stakes is high severity; low-stakes still passes
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blog_tier_4_high_stakes_flagged_high():
    flags = await _run(_metric(
        metric_id="run_rate_usd_billion",  # high-stakes
        unit="usd_billion",
        value=50.0,
        source=_src(
            doc_title="DemandSage / Vidico",
            url="https://www.demandsage.com/instagram-reel-statistics/",
        ),
    ))
    assert len(flags) == 1
    assert flags[0].kind == "source_tier"
    assert flags[0].severity == "high"
    assert "tier 4" in flags[0].evidence[0].lower() or "tier 4" in (flags[0].suggested_fix or "").lower()


@pytest.mark.asyncio
async def test_blog_tier_4_low_stakes_no_flag():
    """Tier-4 source for a non-anchor metric is acceptable."""
    flags = await _run(_metric(
        metric_id="reels_dm_sends_billion",
        unit="users_billion",
        value=1.0,
        source=_src(
            doc_title="DemandSage / Vidico",
            url="https://www.demandsage.com/instagram-reel-statistics/",
        ),
    ))
    assert flags == []


# -----------------------------------------------------------------------------
# Unknown source / alias resolution
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_source_flagged_medium():
    flags = await _run(_metric(
        source=_src(
            doc_title="Some Random Blog 2026",
            url="https://random-blog-nobody-knows.example.com/post",
        ),
    ))
    assert len(flags) == 1
    assert flags[0].kind == "source_tier"
    assert flags[0].severity == "medium"
    assert "sources_registry" in (flags[0].suggested_fix or "")


@pytest.mark.asyncio
async def test_alias_match_via_doc_title_no_url():
    """source.url empty but doc_title matches an alias → resolves to tier 2, no flag."""
    flags = await _run(_metric(
        metric_id="dap_billion",
        source=_src(
            doc_title="Meta Q4 2025 press release",
            url="",
        ),
    ))
    assert flags == []


# -----------------------------------------------------------------------------
# Registry singleton / load-once
# -----------------------------------------------------------------------------

def test_registry_loaded_once_on_initial_import():
    """Module import triggers exactly one load.  Subsequent imports of
    the same module don't reload (the load happens at top-level of the
    module, executed exactly once by the import system)."""
    import importlib
    initial = sst_module._load_count
    # Importing again does not reload (Python caches imports).
    importlib.import_module("baselines.tools.sanitize_source_tier")
    assert sst_module._load_count == initial, (
        f"expected no extra load, got {sst_module._load_count - initial}"
    )


def test_registry_reload_via_test_hook_increments_counter(tmp_path):
    """The test-hook _set_registry_path_for_tests does reload; the
    autouse fixture has already triggered one reload, so calling it
    again should bump the counter by exactly 1."""
    before = sst_module._load_count
    fresh = tmp_path / "another_registry.yaml"
    fresh.write_text(yaml.safe_dump({
        "_meta": {"layer": 5},
        "primary_sources": {
            "x": {"url": "https://x.example.com/", "trust_tier": 3, "aliases": []},
        },
    }, sort_keys=False))
    sst_module._set_registry_path_for_tests(fresh)
    after = sst_module._load_count
    assert after == before + 1


# -----------------------------------------------------------------------------
# readOnlyHint annotation
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_three_tools_registered_all_readonly():
    """After M12b, the server has three sanitize_* tools, all read-only."""
    from baselines.server import baselines_server

    tools = await baselines_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert {"sanitize_schema", "sanitize_policy", "sanitize_source_tier"} <= set(by_name)
    for name in ("sanitize_schema", "sanitize_policy", "sanitize_source_tier"):
        assert by_name[name].annotations.readOnlyHint is True, (
            f"{name} missing readOnlyHint=True"
        )
