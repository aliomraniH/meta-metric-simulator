"""VoyageAI async client wrapper for embeddings + rerank.

Wraps `voyageai.AsyncClient` with the project defaults (voyage-3-large
for general scenario embeddings; rerank-2.5 for two-stage retrieval).
The class is the seam M16's `l8_find_similar_scenarios` /
`l8_compare_scenarios` tools call into.

Defaults (from docs/INSIGHTS_ROADMAP.md §2):
  - voyage-3-large       1024-dim, general purpose
  - voyage-finance-2     1536-dim, finance-tuned (M16 monetization narratives)
  - rerank-2.5           refines top-K from initial cosine retrieval

L2 normalisation: cosine similarity == dot product on L2-normalised
vectors.  We always normalise before storage so retrieval can use
cheap dot products.
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np


_DEFAULT_EMBED_MODEL = "voyage-3-large"
_DEFAULT_RERANK_MODEL = "rerank-2.5"


class VoyageClient:
    """Thin async wrapper around `voyageai.AsyncClient`."""

    def __init__(self, api_key: str | None = None, _client: Any | None = None) -> None:
        """Create a client.

        Args:
            api_key:  Voyage API key.  Defaults to ``VOYAGE_API_KEY`` env var.
            _client:  test seam — inject a pre-built client.  Production
                      callers omit this; tests inject an AsyncMock-shaped
                      stand-in to avoid hitting the real API.
        """
        if _client is not None:
            self._client = _client
            return
        # Lazy import so unit tests that exercise the seam don't require
        # voyageai to be importable at module load time.
        import voyageai

        key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not key:
            raise RuntimeError(
                "VOYAGE_API_KEY is unset.  Set the env var or pass api_key= "
                "explicitly.  For unit tests, construct with _client=<stub>."
            )
        self._client = voyageai.AsyncClient(api_key=key)

    # -- embeddings --------------------------------------------------------

    async def embed_documents(
        self,
        texts: list[str],
        model: str = _DEFAULT_EMBED_MODEL,
    ) -> list[list[float]]:
        """Embed for storage.  ``input_type='document'``.

        Returned vectors are L2-normalised.  Voyage already returns
        normalised vectors but we normalise again defensively — cheap
        and protects against API drift.
        """
        result = await self._client.embed(
            texts=list(texts),
            model=model,
            input_type="document",
        )
        return [self.normalize(v) for v in _embeddings(result)]

    async def embed_query(
        self,
        text: str,
        model: str = _DEFAULT_EMBED_MODEL,
    ) -> list[float]:
        """Embed for retrieval.  ``input_type='query'``."""
        result = await self._client.embed(
            texts=[text],
            model=model,
            input_type="query",
        )
        vecs = _embeddings(result)
        if not vecs:
            return []
        return self.normalize(vecs[0])

    # -- rerank ------------------------------------------------------------

    async def rerank(
        self,
        query: str,
        documents: list[str],
        model: str = _DEFAULT_RERANK_MODEL,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Rerank ``documents`` against ``query``.

        Returns at most ``top_k`` `{document, relevance_score, index}`
        dicts ordered by descending relevance_score.
        """
        result = await self._client.rerank(
            query=query,
            documents=list(documents),
            model=model,
            top_k=top_k,
        )
        out: list[dict[str, Any]] = []
        # voyageai returns either an object with `.results` or a dict.
        results = (
            getattr(result, "results", None)
            if not isinstance(result, dict)
            else result.get("results")
        ) or []
        for r in results:
            out.append({
                "document": _attr(r, "document", ""),
                "relevance_score": _attr(r, "relevance_score", 0.0),
                "index": _attr(r, "index", -1),
            })
        # Defensive: enforce top_k slice + descending order.
        out.sort(key=lambda d: d["relevance_score"], reverse=True)
        return out[:top_k]

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def normalize(vec: list[float]) -> list[float]:
        """L2-normalise a vector.  Returns the input unchanged when
        ``||vec|| == 0`` (avoids NaN division)."""
        v = np.asarray(vec, dtype=float)
        norm = float(np.linalg.norm(v))
        if norm == 0.0:
            return list(v.tolist())
        return (v / norm).tolist()


# ---------------------------------------------------------------------------
# Helpers — pure
# ---------------------------------------------------------------------------

def _embeddings(result: Any) -> list[list[float]]:
    """Pull a list[list[float]] of embeddings out of a Voyage SDK response.

    Tolerates both object-shaped (`result.embeddings`) and dict-shaped
    (`result["embeddings"]`) responses so tests can inject either."""
    if isinstance(result, dict):
        embs = result.get("embeddings") or []
    else:
        embs = getattr(result, "embeddings", None) or []
    return [list(e) for e in embs]


def _attr(obj: Any, name: str, default: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
