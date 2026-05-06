"""find_similar_scenarios — two-stage Voyage retrieval.

Pattern from docs/INSIGHTS_ROADMAP.md §2: embed → cosine top-100 →
rerank-2.5 top-K.  The cosine stage is cheap and approximate; the
rerank stage is expensive and precise.

Annotated `readOnlyHint=True` — pure retrieval, no writes.  The
narrator + synthesizer are the only tools that produce canonical
state in Layer 8.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from insights.server import insights_server
from insights.voyage_client import VoyageClient

log = logging.getLogger(__name__)


_DEFAULT_K = 5
_PRE_RERANK_TOP_N = 100   # cosine-similarity stage cap
_CANDIDATE_LIMIT = 200    # bound the SELECT to the most recent scenarios


@insights_server.tool(
    annotations={"readOnlyHint": True},
    exclude_args=["voyage_client"],
)
async def find_similar_scenarios(
    scenario_id: str,
    k: int = _DEFAULT_K,
    voyage_client: VoyageClient | None = None,
) -> list[dict[str, Any]]:
    """Return the K most similar scenarios via voyage embedding + rerank.

    Args:
        scenario_id:    the source scenario.  Excluded from results so
                        a scenario is never its own neighbour.
        k:              top-K cap on the rerank output.
        voyage_client:  injectable for tests; when omitted, a default
                        VoyageClient is constructed via VOYAGE_API_KEY.

    Returns:
        List of `{scenario_id, name, description, similarity_score, rank}`
        sorted by rank ascending (rank 1 = most similar).  Empty list
        when there are no candidates other than the source.
    """
    source = await _load_scenario_doc(scenario_id)
    if source is None:
        log.warning("find_similar_scenarios: source %s not in scenarios table", scenario_id)
        return []

    candidates = await _load_candidate_docs(exclude_scenario_id=scenario_id)
    if not candidates:
        log.warning("find_similar_scenarios: no candidates other than the source")
        return []

    client = voyage_client or VoyageClient()

    # Stage 1: embed source as query, candidates as documents.
    query_vec = await client.embed_query(source["doc"])
    candidate_vecs = await client.embed_documents([c["doc"] for c in candidates])

    # Cosine similarity = dot product on L2-normalised vectors.
    scored: list[tuple[float, dict[str, Any]]] = []
    for vec, cand in zip(candidate_vecs, candidates):
        score = sum(a * b for a, b in zip(query_vec, vec))
        scored.append((score, cand))
    scored.sort(key=lambda t: t[0], reverse=True)
    top_n = scored[:_PRE_RERANK_TOP_N]

    # Stage 2: rerank-2.5 over the cosine-survivors.
    docs_for_rerank = [c["doc"] for _, c in top_n]
    reranked = await client.rerank(
        query=source["doc"], documents=docs_for_rerank, top_k=k,
    )

    out: list[dict[str, Any]] = []
    for rank, r in enumerate(reranked, start=1):
        idx = r.get("index", -1)
        if not (0 <= idx < len(top_n)):
            continue
        cand = top_n[idx][1]
        out.append({
            "scenario_id":      cand["scenario_id"],
            "name":             cand["name"],
            "description":      cand["description"],
            "similarity_score": float(r.get("relevance_score", 0.0)),
            "rank":             rank,
        })
    return out


# ---------------------------------------------------------------------------
# Helpers — pull rows from the scenarios table and shape into "doc" strings
# ---------------------------------------------------------------------------

async def _load_scenario_doc(scenario_id: str) -> dict[str, Any] | None:
    """Load one scenario row + build its embedding document string."""
    try:
        from infra.db import scenarios as scenarios_table
        from infra.db import session as _session
    except Exception as exc:  # noqa: BLE001
        log.debug("scenarios load skipped (no infra.db): %s", exc)
        return None
    try:
        async with _session() as s:
            result = await s.execute(
                select(scenarios_table).where(
                    scenarios_table.c.scenario_id == scenario_id
                )
            )
            row = result.mappings().one_or_none()
    except Exception as exc:  # noqa: BLE001
        log.warning("scenarios load failed: %s", exc)
        return None
    if row is None:
        return None
    return {
        "scenario_id": row["scenario_id"],
        "name":        row["name"] or "",
        "description": row["description"] or "",
        "doc":         _build_doc(row),
    }


async def _load_candidate_docs(*, exclude_scenario_id: str) -> list[dict[str, Any]]:
    """Load up to _CANDIDATE_LIMIT recent scenarios, excluding the source."""
    try:
        from infra.db import scenarios as scenarios_table
        from infra.db import session as _session
    except Exception as exc:  # noqa: BLE001
        log.debug("scenarios load skipped (no infra.db): %s", exc)
        return []
    try:
        async with _session() as s:
            result = await s.execute(
                select(scenarios_table)
                .where(scenarios_table.c.scenario_id != exclude_scenario_id)
                .order_by(scenarios_table.c.created_at.desc())
                .limit(_CANDIDATE_LIMIT)
            )
            rows = result.mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("scenarios load failed: %s", exc)
        return []
    return [
        {
            "scenario_id": r["scenario_id"],
            "name":        r["name"] or "",
            "description": r["description"] or "",
            "doc":         _build_doc(r),
        }
        for r in rows
    ]


def _build_doc(row: Any) -> str:
    """Construct the embedding document string for a scenarios row.

    Format: ``"{name}: {description}. Perturbations: {perts}. Audience:
    {audience}. Horizon: {horizon}."`` — keeps trajectory-relevant
    fields visible to the embedder per docs/INSIGHTS_ROADMAP.md §1.
    """
    name = row["name"] or ""
    description = row["description"] or ""
    perts = row.get("perturbations") if isinstance(row, dict) else row["perturbations"]
    audience = (
        row.get("audience_filters") if isinstance(row, dict) else row["audience_filters"]
    )
    horizon = (
        row.get("time_horizon") if isinstance(row, dict) else row["time_horizon"]
    ) or ""
    perts_summary = _summarise_list(perts)
    audience_summary = _summarise_list(audience)
    return (
        f"{name}: {description}. "
        f"Perturbations: {perts_summary}. "
        f"Audience: {audience_summary}. "
        f"Horizon: {horizon}."
    )


def _summarise_list(items: Any) -> str:
    if not items:
        return "(none)"
    if isinstance(items, str):
        return items
    try:
        return "; ".join(
            f"{i.get('target') or i.get('dim') or 'item'}={i.get('op') or i.get('value') or ''}"
            for i in items
        )
    except Exception:  # noqa: BLE001
        return str(items)
