"""Real-API roundtrip test for insights/voyage_client.py.

Skipped by default — runs only when BOTH ``VOYAGE_API_KEY`` and
``RUN_INTEGRATION_TESTS`` are set in the environment.  CI wires
both for the integration job; local development can opt in with::

    VOYAGE_API_KEY=... RUN_INTEGRATION_TESTS=1 pytest \\
        tests/integration/test_voyage_roundtrip.py -v

The unit tests in tests/insights/test_voyage_client.py exercise the
wrapper logic against a stub client — those run on every CI invocation.
"""
from __future__ import annotations

import os

import pytest

from insights.voyage_client import VoyageClient


_GATE = pytest.mark.skipif(
    not os.environ.get("VOYAGE_API_KEY") or not os.environ.get("RUN_INTEGRATION_TESTS"),
    reason="Voyage roundtrip requires VOYAGE_API_KEY and RUN_INTEGRATION_TESTS",
)


@_GATE
@pytest.mark.asyncio
async def test_voyage_real_embedding_roundtrip():
    """Sanity check that the VoyageClient works against the real API.

    Verifies:
      * embed_query returns a unit-length 1024-dim vector for voyage-3-large.
      * embed_documents returns one vector per input.
      * rerank returns ≤ top_k results sorted by relevance_score desc.
    """
    client = VoyageClient()
    vec = await client.embed_query("test scenario about ad revenue spike")
    assert len(vec) == 1024
    assert all(isinstance(x, float) for x in vec)
    norm_sq = sum(x * x for x in vec)
    assert 0.99 <= norm_sq <= 1.01, f"expected unit-length vector; got |v|^2 = {norm_sq}"

    docs = [
        "scenario A: ad revenue spiked on day 5 by 18%",
        "scenario B: integrity incident at tick 26 reduced impressions",
    ]
    embeddings = await client.embed_documents(docs)
    assert len(embeddings) == 2
    assert all(len(e) == 1024 for e in embeddings)

    reranked = await client.rerank("ad revenue spike", docs, top_k=2)
    assert len(reranked) == 2
    assert reranked[0]["relevance_score"] >= reranked[1]["relevance_score"]
