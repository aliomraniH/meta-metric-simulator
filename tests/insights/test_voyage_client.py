"""Tests for insights/voyage_client.py.

All tests use a stub Voyage client injected via the ``_client`` test
seam — no real API calls.  The roundtrip integration test against the
real Voyage API lives in tests/integration/test_voyage_roundtrip.py
behind the VOYAGE_API_KEY + RUN_INTEGRATION_TESTS env-var gates.
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock

import pytest

from insights.voyage_client import VoyageClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_client() -> AsyncMock:
    """An AsyncMock whose `embed` and `rerank` are AsyncMock-shaped."""
    c = AsyncMock()
    c.embed = AsyncMock()
    c.rerank = AsyncMock()
    return c


def _embed_response(vectors: list[list[float]]) -> dict:
    return {"embeddings": vectors}


def _rerank_response(items: list[dict]) -> dict:
    return {"results": items}


# ---------------------------------------------------------------------------
# embed_documents / embed_query — input_type wiring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_documents_uses_input_type_document():
    stub = _stub_client()
    stub.embed.return_value = _embed_response([[1.0, 0.0, 0.0]])
    client = VoyageClient(_client=stub)
    await client.embed_documents(["scenario A description"])
    stub.embed.assert_awaited_once()
    kwargs = stub.embed.call_args.kwargs
    assert kwargs["input_type"] == "document"
    assert kwargs["texts"] == ["scenario A description"]


@pytest.mark.asyncio
async def test_embed_query_uses_input_type_query():
    stub = _stub_client()
    stub.embed.return_value = _embed_response([[0.0, 1.0, 0.0]])
    client = VoyageClient(_client=stub)
    await client.embed_query("ad revenue spike")
    stub.embed.assert_awaited_once()
    kwargs = stub.embed.call_args.kwargs
    assert kwargs["input_type"] == "query"


# ---------------------------------------------------------------------------
# rerank — top_k slicing + descending order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rerank_returns_top_k():
    """SDK returns 10 items; we ask for 5; receive exactly 5 back, sorted desc."""
    stub = _stub_client()
    stub.rerank.return_value = _rerank_response([
        {"document": f"doc-{i}", "relevance_score": float(10 - i), "index": i}
        for i in range(10)
    ])
    client = VoyageClient(_client=stub)
    out = await client.rerank("query", [f"doc-{i}" for i in range(10)], top_k=5)
    assert len(out) == 5
    scores = [r["relevance_score"] for r in out]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_rerank_handles_object_results():
    """Voyage may return a list of objects rather than dicts.  Both shapes work."""
    class _R:
        def __init__(self, doc, score, idx):
            self.document, self.relevance_score, self.index = doc, score, idx

    stub = _stub_client()
    stub.rerank.return_value = _rerank_response([
        _R("doc-0", 0.9, 0),
        _R("doc-1", 0.7, 1),
    ])
    client = VoyageClient(_client=stub)
    out = await client.rerank("q", ["doc-0", "doc-1"], top_k=2)
    assert out[0]["document"] == "doc-0"
    assert out[0]["relevance_score"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# normalize — pure helper
# ---------------------------------------------------------------------------

def test_normalize_unit_vector():
    out = VoyageClient.normalize([3.0, 4.0])
    assert out[0] == pytest.approx(0.6)
    assert out[1] == pytest.approx(0.8)
    assert math.isclose(sum(x * x for x in out), 1.0, rel_tol=1e-9)


def test_normalize_zero_vector():
    """Zero-vector input must NOT raise ZeroDivisionError or produce NaN."""
    out = VoyageClient.normalize([0.0, 0.0, 0.0])
    assert out == [0.0, 0.0, 0.0]


def test_normalize_already_normalised():
    out = VoyageClient.normalize([1.0, 0.0, 0.0])
    assert out == pytest.approx([1.0, 0.0, 0.0])


# ---------------------------------------------------------------------------
# Default model wiring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_default_model_voyage_3_large():
    stub = _stub_client()
    stub.embed.return_value = _embed_response([[1.0, 0.0]])
    client = VoyageClient(_client=stub)
    await client.embed_query("anything")
    kwargs = stub.embed.call_args.kwargs
    assert kwargs["model"] == "voyage-3-large"


@pytest.mark.asyncio
async def test_explicit_finance_model_passes_through():
    """Calling embed_documents with model='voyage-finance-2' must reach the SDK."""
    stub = _stub_client()
    stub.embed.return_value = _embed_response([[1.0, 0.0]])
    client = VoyageClient(_client=stub)
    await client.embed_documents(["finance text"], model="voyage-finance-2")
    kwargs = stub.embed.call_args.kwargs
    assert kwargs["model"] == "voyage-finance-2"


# ---------------------------------------------------------------------------
# embed result is L2-normalised
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_documents_returns_l2_normalised_vectors():
    """Defensively normalise even if the SDK already returns unit vectors."""
    stub = _stub_client()
    stub.embed.return_value = _embed_response([[3.0, 4.0]])
    client = VoyageClient(_client=stub)
    out = await client.embed_documents(["x"])
    assert len(out) == 1
    norm = math.sqrt(sum(v * v for v in out[0]))
    assert norm == pytest.approx(1.0, rel=1e-9)
