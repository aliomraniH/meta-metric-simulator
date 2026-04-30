"""Tests for baselines/tools/sanitize_policy.py."""
from __future__ import annotations

import pytest

from baselines.schemas import ExtractedMetric, ExtractedMetricSource, SanitizerFlag
from baselines.tools.sanitize_policy import sanitize_policy


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
    return await sanitize_policy(extracted)


# -----------------------------------------------------------------------------
# Clean pass
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clean_metric_no_flags():
    flags = await _run(_metric())
    assert flags == []


# -----------------------------------------------------------------------------
# Unsourced number
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unsourced_number_empty_quoted_text():
    flags = await _run(_metric(source=_src(quoted_text="")))
    policy = [f for f in flags if f.kind == "policy"]
    assert any(f.severity == "high" for f in policy)
    # The quoted-text-empty flag specifically says "no anchor".
    assert any("no anchor" in (e or "") for f in policy for e in f.evidence)


@pytest.mark.asyncio
async def test_quoted_text_no_digits_flagged_high():
    flags = await _run(_metric(source=_src(quoted_text="ad revenue grew strongly this quarter")))
    high_policy = [f for f in flags if f.kind == "policy" and f.severity == "high"]
    assert len(high_policy) == 1
    assert "no digits" in high_policy[0].evidence[0]


# -----------------------------------------------------------------------------
# Primary-source preference
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aggregator_url_flagged_medium():
    flags = await _run(_metric(source=_src(url="https://big-aggregator.com/some-roundup")))
    med_policy = [f for f in flags if f.kind == "policy" and f.severity == "medium"]
    assert len(med_policy) >= 1


@pytest.mark.asyncio
async def test_via_prefix_in_doc_title_flagged_medium():
    flags = await _run(_metric(source=_src(doc_title="Reels Stats via eMarketer")))
    med_policy = [f for f in flags if f.kind == "policy" and f.severity == "medium"]
    assert len(med_policy) == 1
    assert "via" in med_policy[0].evidence[0].lower()


# -----------------------------------------------------------------------------
# Competitor metric must cite asymmetry
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_competitor_metric_missing_asymmetry_flagged_high():
    flags = await _run(_metric(
        metric_id="competitor_tiktok_arpu",
        source=_src(quoted_text="TikTok US ARPU was $96.71 in 2024"),
    ))
    high_policy = [f for f in flags if f.kind == "policy" and f.severity == "high"]
    assert len(high_policy) == 1
    assert "view-definition asymmetry" in high_policy[0].evidence[0]


@pytest.mark.asyncio
async def test_competitor_metric_with_asymmetry_passes():
    flags = await _run(_metric(
        metric_id="competitor_tiktok_arpu",
        source=_src(quoted_text=(
            "TikTok US ARPU was $96.71 in 2024. Note view-definition asymmetry "
            "across IG/FB/Shorts/TikTok per §6.2."
        )),
    ))
    high_policy = [f for f in flags if f.kind == "policy" and f.severity == "high"]
    assert high_policy == []


@pytest.mark.asyncio
async def test_competitor_metric_with_asymmetry_in_doc_title_passes():
    flags = await _run(_metric(
        metric_id="shorts_daily_views_billion",
        value=200.0,
        unit="count",
        source=_src(
            doc_title="YouTube Shorts daily views — view definition asymmetry note",
            quoted_text="200B daily views (Cannes Lions June 2025)",
        ),
    ))
    high_policy = [f for f in flags if f.kind == "policy" and f.severity == "high"]
    assert high_policy == []


# -----------------------------------------------------------------------------
# Forbidden domains
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forbidden_source_twitter_flagged_critical():
    flags = await _run(_metric(source=_src(url="https://twitter.com/somepost/status/123")))
    crit = [f for f in flags if f.kind == "policy" and f.severity == "critical"]
    assert len(crit) == 1
    assert "forbidden-domain" in crit[0].evidence[0]


@pytest.mark.asyncio
async def test_forbidden_source_reddit_flagged_critical():
    flags = await _run(_metric(source=_src(url="https://www.reddit.com/r/MetaInvestors/comments/abc")))
    crit = [f for f in flags if f.kind == "policy" and f.severity == "critical"]
    assert len(crit) == 1


@pytest.mark.asyncio
async def test_forbidden_source_x_dot_com_flagged_critical():
    flags = await _run(_metric(source=_src(url="https://x.com/zuck/status/9999")))
    crit = [f for f in flags if f.kind == "policy" and f.severity == "critical"]
    assert len(crit) == 1


# -----------------------------------------------------------------------------
# Multi-violation aggregation
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_violations_returned():
    """Empty quoted_text + Twitter URL → both flags fire."""
    flags = await _run(_metric(source=_src(quoted_text="", url="https://twitter.com/x")))
    severities = {f.severity for f in flags}
    assert "high" in severities      # empty quoted_text
    assert "critical" in severities  # twitter.com
    assert all(f.kind == "policy" for f in flags)
