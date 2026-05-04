"""Tests for engine/tools/lookup_seasonality.py."""
from __future__ import annotations

import pytest

from engine.tools.lookup_seasonality import lookup_seasonality


KNOWN_PERIODS = ("Q4 2025", "Q1 2026", "summer_vacation_us", "back_to_school_us")
EXPECTED_KEYS = {
    "ad_demand_multiplier",
    "viewing_time_multiplier",
    "content_supply_multiplier",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("period", KNOWN_PERIODS)
async def test_known_periods_return_multipliers(period):
    """Each documented period must return a non-1.0 multiplier on at least one axis."""
    result = await lookup_seasonality(period)
    for key in EXPECTED_KEYS:
        assert key in result
    deviates = any(result[key] != 1.0 for key in EXPECTED_KEYS)
    assert deviates, f"{period} returned all-neutral multipliers — registry probably broken"
    assert result["note"] == "matched"


@pytest.mark.asyncio
async def test_unknown_period_returns_neutral():
    """Unknown period → all-1.0 multipliers + note='period_unknown'."""
    result = await lookup_seasonality("never_heard_of_it")
    for key in EXPECTED_KEYS:
        assert result[key] == 1.0
    assert result["note"] == "period_unknown"


@pytest.mark.asyncio
async def test_response_has_expected_keys():
    """Every response carries the three multiplier keys + period/region/note."""
    result = await lookup_seasonality("Q4 2025")
    assert EXPECTED_KEYS <= set(result.keys())
    assert "period" in result
    assert "region" in result
    assert "note" in result


@pytest.mark.asyncio
async def test_q4_2025_ad_demand_uplift():
    """Q4 2025 should show ad-demand seasonality > 1.0 (well-known holiday lift)."""
    result = await lookup_seasonality("Q4 2025")
    assert result["ad_demand_multiplier"] > 1.0


@pytest.mark.asyncio
async def test_tool_registered_readonly():
    from engine.server import engine_server
    tools = await engine_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert by_name["lookup_seasonality"].annotations.readOnlyHint is True
