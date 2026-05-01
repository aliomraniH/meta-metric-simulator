"""Tests for curation/tools/sanitize_constitution.py.

Each constitutional rule has a positive (violated → flagged) and a
negative (clean → no flag) test where applicable.  Rule severities
are pinned because the M13b synthesizer's critique-revise loop only
gates on `high` and `critical`.
"""
from __future__ import annotations

import pytest

from curation.schemas import ConstitutionFlag, DiffProposal
from curation.tools.sanitize_constitution import sanitize_constitution


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        confidence=0.9,
    )
    base.update(overrides)
    return DiffProposal(**base)


async def _run(diff: DiffProposal) -> list[ConstitutionFlag]:
    return await sanitize_constitution(diff)


# ---------------------------------------------------------------------------
# Clean pass
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clean_diff_no_flags():
    flags = await _run(_diff())
    assert flags == []


# ---------------------------------------------------------------------------
# Rule 2: uncited_numeric (critical)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uncited_numeric_proposal():
    """A numeric-field proposal with empty cited_evidence is critical."""
    flags = await _run(_diff(
        field="trust_tier",
        proposed_value=2,
        cited_evidence=[],
    ))
    uncited = [f for f in flags if f.rule == "uncited_numeric"]
    assert len(uncited) == 1
    assert uncited[0].severity == "critical"


# ---------------------------------------------------------------------------
# Rule 6: secondary_aggregator_used (high)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aggregator_only_on_high_stakes():
    """High-stakes registry entry (id contains 'dap_billion') with only
    eMarketer URLs in cited_evidence → high-severity flag."""
    flags = await _run(_diff(
        registry_entry_id="meta_dap_billion_q4_2025",
        cited_evidence=[
            {"url": "https://www.emarketer.com/page-1", "page": 1, "quoted_text": "DAP 3.58B"},
            {"url": "https://www.statista.com/page-2", "page": 1, "quoted_text": "DAP rose"},
        ],
    ))
    secondary = [f for f in flags if f.rule == "secondary_aggregator_used"]
    assert len(secondary) == 1
    assert secondary[0].severity == "high"


@pytest.mark.asyncio
async def test_aggregator_only_on_low_stakes():
    """Low-stakes registry entry + only eMarketer URLs → no flag.
    Tier 3 is acceptable for non-anchor metrics."""
    flags = await _run(_diff(
        registry_entry_id="reels_engagement_color_palette_2026",
        cited_evidence=[
            {"url": "https://www.emarketer.com/page", "page": 1, "quoted_text": "engagement note"},
        ],
    ))
    secondary = [f for f in flags if f.rule == "secondary_aggregator_used"]
    assert secondary == []


# ---------------------------------------------------------------------------
# Rule 4: competitor_missing_asymmetry (high)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_competitor_missing_asymmetry():
    """Competitor metric without asymmetry mention → high severity flag."""
    flags = await _run(_diff(
        registry_entry_id="competitor_tiktok_arpu",
        rationale="TikTok US ARPU is the standard benchmark for cross-platform.",
        cited_evidence=[{
            "url": "https://www.sensortower.com/x",
            "page": 1,
            "quoted_text": "TikTok ARPU was $96.71 in 2024",
        }],
    ))
    asym = [f for f in flags if f.rule == "competitor_missing_asymmetry"]
    assert len(asym) == 1
    assert asym[0].severity == "high"


@pytest.mark.asyncio
async def test_competitor_with_asymmetry_passes():
    """Same registry id but asymmetry mention present → no flag."""
    flags = await _run(_diff(
        registry_entry_id="competitor_tiktok_arpu",
        rationale=(
            "TikTok US ARPU is the standard benchmark; note view-definition "
            "asymmetry across IG/FB/Shorts/TikTok per §6.2."
        ),
        cited_evidence=[{
            "url": "https://www.sensortower.com/x",
            "page": 1,
            "quoted_text": "TikTok ARPU was $96.71 in 2024",
        }],
    ))
    asym = [f for f in flags if f.rule == "competitor_missing_asymmetry"]
    assert asym == []


# ---------------------------------------------------------------------------
# Rule 5: forbidden_domain (critical)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forbidden_source_in_evidence():
    """Twitter URL anywhere in cited_evidence → critical flag."""
    flags = await _run(_diff(
        cited_evidence=[
            {"url": "https://twitter.com/zuck/status/12345", "page": None,
             "quoted_text": "tweet"},
        ],
    ))
    forb = [f for f in flags if f.rule == "forbidden_domain"]
    assert len(forb) == 1
    assert forb[0].severity == "critical"


# ---------------------------------------------------------------------------
# Rule 1 proxy: short rationale (medium)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_short_rationale():
    """A 2-character rationale → medium severity flag (proxy for Rule 1)."""
    flags = await _run(_diff(rationale="ok"))
    short = [f for f in flags if f.rule == "primary_source_required"]
    assert len(short) == 1
    assert short[0].severity == "medium"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_four_tools_registered_all_readonly():
    """After M13a, the curation server has four tools, all read-only."""
    from curation.server import curation_server

    tools = await curation_server.list_tools()
    by_name = {t.name: t for t in tools}
    expected = {"web_search", "web_fetch", "diff_proposal", "sanitize_constitution"}
    assert expected <= set(by_name)
    for name in expected:
        assert by_name[name].annotations.readOnlyHint is True, (
            f"{name} missing readOnlyHint=True"
        )
