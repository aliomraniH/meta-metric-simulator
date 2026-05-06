"""Tests for insights/tools/find_similar_scenarios.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import insights.tools.find_similar_scenarios as fs_module
from insights.tools.find_similar_scenarios import find_similar_scenarios


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_voyage(rerank_results=None, *, query_vec=None, candidate_vecs=None):
    """Build a stub VoyageClient-shaped object."""
    client = AsyncMock()
    client.embed_query = AsyncMock(return_value=query_vec or [1.0, 0.0])
    client.embed_documents = AsyncMock(return_value=candidate_vecs or [[1.0, 0.0]])
    client.rerank = AsyncMock(return_value=rerank_results or [])
    return client


def _candidate_doc(scenario_id: str, name: str = "x", description: str = "y") -> dict:
    return {
        "scenario_id": scenario_id,
        "name": name,
        "description": description,
        "doc": f"{name}: {description}",
    }


# ---------------------------------------------------------------------------
# 1. Top-K
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_similar_returns_top_k(monkeypatch):
    """When rerank returns 5 results we pass them through as-is."""
    source = _candidate_doc("source-id", "SourceScenario")
    candidates = [_candidate_doc(f"cand-{i}") for i in range(20)]

    async def fake_load_source(scenario_id):
        return source if scenario_id == "source-id" else None

    async def fake_load_candidates(*, exclude_scenario_id):
        return [c for c in candidates if c["scenario_id"] != exclude_scenario_id]

    monkeypatch.setattr(fs_module, "_load_scenario_doc", fake_load_source)
    monkeypatch.setattr(fs_module, "_load_candidate_docs", fake_load_candidates)

    voyage = _fake_voyage(
        query_vec=[1.0, 0.0],
        candidate_vecs=[[1.0 - 0.01 * i, 0.0] for i in range(20)],
        rerank_results=[
            {"document": f"doc-{i}", "relevance_score": 0.9 - 0.1 * i, "index": i}
            for i in range(5)
        ],
    )
    out = await find_similar_scenarios("source-id", k=5, voyage_client=voyage)
    assert len(out) == 5
    assert all("scenario_id" in r for r in out)
    assert all(r["rank"] == i + 1 for i, r in enumerate(out))
    # Descending similarity_score.
    scores = [r["similarity_score"] for r in out]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# 2. Source excluded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_similar_excludes_source(monkeypatch):
    """The source scenario_id must never appear in the result list."""
    source = _candidate_doc("source-id", "Source")
    candidates = [_candidate_doc("source-id"), _candidate_doc("other-1")]

    async def fake_load_source(scenario_id):
        return source

    async def fake_load_candidates(*, exclude_scenario_id):
        return [c for c in candidates if c["scenario_id"] != exclude_scenario_id]

    monkeypatch.setattr(fs_module, "_load_scenario_doc", fake_load_source)
    monkeypatch.setattr(fs_module, "_load_candidate_docs", fake_load_candidates)

    voyage = _fake_voyage(rerank_results=[
        {"document": "x", "relevance_score": 0.9, "index": 0},
    ])
    out = await find_similar_scenarios("source-id", k=5, voyage_client=voyage)
    sids = {r["scenario_id"] for r in out}
    assert "source-id" not in sids


# ---------------------------------------------------------------------------
# 3. rerank-2.5 used
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_similar_uses_voyage_rerank(monkeypatch):
    """rerank() is invoked — model defaults to rerank-2.5 inside VoyageClient."""
    source = _candidate_doc("source-id", "Source")

    async def fake_load_source(scenario_id):
        return source

    async def fake_load_candidates(*, exclude_scenario_id):
        return [_candidate_doc("cand-1")]

    monkeypatch.setattr(fs_module, "_load_scenario_doc", fake_load_source)
    monkeypatch.setattr(fs_module, "_load_candidate_docs", fake_load_candidates)

    voyage = _fake_voyage(rerank_results=[
        {"document": "doc", "relevance_score": 0.7, "index": 0},
    ])
    await find_similar_scenarios("source-id", k=3, voyage_client=voyage)
    assert voyage.rerank.await_count == 1
    rerank_kwargs = voyage.rerank.call_args.kwargs
    # top_k threaded through to the rerank stage.
    assert rerank_kwargs["top_k"] == 3


# ---------------------------------------------------------------------------
# 4. Empty candidates → empty list (with warning)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_similar_handles_empty_candidates(monkeypatch):
    async def fake_load_source(scenario_id):
        return _candidate_doc("source-id")

    async def fake_load_candidates(*, exclude_scenario_id):
        return []  # only the source exists

    monkeypatch.setattr(fs_module, "_load_scenario_doc", fake_load_source)
    monkeypatch.setattr(fs_module, "_load_candidate_docs", fake_load_candidates)

    voyage = _fake_voyage()
    out = await find_similar_scenarios("source-id", voyage_client=voyage)
    assert out == []
    # Voyage was never called — no candidates means no embedding cost.
    assert voyage.embed_query.await_count == 0


# ---------------------------------------------------------------------------
# 5. Source not found → empty list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_similar_unknown_source(monkeypatch):
    async def fake_load_source(scenario_id):
        return None

    async def fake_load_candidates(*, exclude_scenario_id):
        return [_candidate_doc("c1")]

    monkeypatch.setattr(fs_module, "_load_scenario_doc", fake_load_source)
    monkeypatch.setattr(fs_module, "_load_candidate_docs", fake_load_candidates)

    voyage = _fake_voyage()
    out = await find_similar_scenarios("missing-id", voyage_client=voyage)
    assert out == []


# ---------------------------------------------------------------------------
# 6. Tool registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_similar_registered_readonly():
    from insights.server import insights_server
    tools = await insights_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert "find_similar_scenarios" in by_name
    assert by_name["find_similar_scenarios"].annotations.readOnlyHint is True
