"""Tests for engine/tools/get_audience_definition.py."""
from __future__ import annotations

import pytest

from engine.tools.get_audience_definition import (
    _AUDIENCE_REGISTRY,
    get_audience_definition,
)


KNOWN_AUDIENCES = (
    "teens", "us_only", "new_users", "core_users",
    "snackers", "lurkers", "creators_micro_plus",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", KNOWN_AUDIENCES)
async def test_known_audiences_resolve(name):
    """Each documented audience must resolve to a {dim, op, value} triple."""
    result = await get_audience_definition(name)
    assert set(result.keys()) == {"dim", "op", "value"}
    assert result["dim"]
    assert result["op"]
    assert result["value"] is not None


@pytest.mark.asyncio
async def test_unknown_audience_raises():
    """Unknown name → ValueError listing every available audience."""
    with pytest.raises(ValueError) as excinfo:
        await get_audience_definition("invented_segment")
    msg = str(excinfo.value)
    for known in KNOWN_AUDIENCES:
        assert known in msg, f"available audience {known!r} missing from error message"


@pytest.mark.asyncio
async def test_returns_independent_copy():
    """Mutating a returned dict must not poison the registry."""
    a = await get_audience_definition("teens")
    a["dim"] = "MUTATED"
    b = await get_audience_definition("teens")
    assert b["dim"] == "viewer_segment"


@pytest.mark.asyncio
async def test_case_insensitive():
    a = await get_audience_definition("TEENS")
    b = await get_audience_definition("teens")
    assert a == b


def test_registry_has_all_documented_audiences():
    """Smoke-check: the registry covers every name documented in the prompt."""
    assert set(KNOWN_AUDIENCES) <= set(_AUDIENCE_REGISTRY.keys())


@pytest.mark.asyncio
async def test_tool_registered_readonly():
    from engine.server import engine_server
    tools = await engine_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert by_name["get_audience_definition"].annotations.readOnlyHint is True
