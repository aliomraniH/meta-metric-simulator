"""web_search — primary-source search, allowed_domains-constrained.

The tool wraps the Anthropic web_search server-side tool with a tight
allowed_domains list and a max_uses cap.  Cost discipline per
architecture_research.pdf §6.5: bounded search rounds, no unbounded
discovery.  Per AGENTIC_ARCHITECTURE_INDEX.md §2.3 the curation
refresher agent uses this to gather candidate URLs before the
citations-enabled web_fetch call (PDF §7.3 two-call pattern).

Annotated `readOnlyHint=True` — search produces no canonical-state
writes.  The synthesizer (M13b) is what writes; this tool only
returns search hits.
"""
from __future__ import annotations

import logging
from typing import Any

from curation.server import curation_server

log = logging.getLogger(__name__)


# Allowed domains for primary-source search.  Tier 1/2 first, tier 3
# (aggregators) at the end — usable for context but never as the sole
# source for high-stakes metrics (see curation/prompts/refresher.md
# Rule 6).  Adding a domain is a deliberate review step.
ALLOWED_DOMAINS: tuple[str, ...] = (
    # Tier 1 — SEC filings
    "sec.gov",
    "www.sec.gov",
    # Tier 2 — Earnings call transcripts
    "investor.atmeta.com",
    "investor.fb.com",  # legacy
    "s23.q4cdn.com",
    "s21.q4cdn.com",
    # Tier 2 — Company IR / press
    "about.fb.com",
    "newsroom.fb.com",
    "engineering.fb.com",
    "ai.meta.com",
    "transparency.meta.com",
    # Tier 3 — Aggregators (context only; NEVER sole source for high-stakes)
    "businessofapps.com",
    "emarketer.com",
    "statista.com",
    "sensortower.com",
)

_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_RESULTS = 8
_DEFAULT_MAX_TOKENS = 1024


@curation_server.tool(annotations={"readOnlyHint": True})
async def web_search(
    query: str,
    ctx: Any,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> list[dict[str, str]]:
    """Search for primary-source evidence, constrained to allowed domains.

    Args:
        query:        the search string.  The curation-refresher writes
                      this from the registry entry's metric_id + period.
        ctx:          FastMCP / MCP-sampling Context.  ctx.sample
                      forwards the search to the orchestrator's Claude
                      with the web_search server-side tool enabled.
        max_results:  cap on the number of hits returned.  Defaults
                      to 8 — the cost-discipline cap.

    Returns:
        List of `{url, title, snippet}` dicts, ordered by relevance.
        Empty list when no hits are in the allowed-domain set.
    """
    capped = max(1, min(int(max_results), _DEFAULT_MAX_RESULTS))
    response = await ctx.sample(
        model=_DEFAULT_MODEL,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Search for primary-source evidence for: {query}\n\n"
                    "Use the web_search tool. Return up to "
                    f"{capped} results. Prefer SEC filings and earnings "
                    "transcripts over aggregator sites."
                ),
            }
        ],
        tools=[{
            "type": "web_search_20250918",
            "name": "web_search",
            "max_uses": capped,
            "allowed_domains": list(ALLOWED_DOMAINS),
        }],
        max_tokens=_DEFAULT_MAX_TOKENS,
    )
    return _extract_search_hits(response)


def _extract_search_hits(response: Any) -> list[dict[str, str]]:
    """Pull `{url, title, snippet}` triples from web_search tool_result blocks."""
    hits: list[dict[str, str]] = []
    blocks = _content_blocks(response)
    for block in blocks:
        # Anthropic emits search results inside server_tool_use / web_search_tool_result blocks.
        btype = _block_field(block, "type")
        if btype not in {"web_search_tool_result", "tool_result"}:
            continue
        content = _block_field(block, "content") or []
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("source") or ""
            if not url:
                continue
            hits.append({
                "url": str(url),
                "title": str(item.get("title", "")),
                "snippet": str(item.get("snippet") or item.get("text") or ""),
            })
    return hits


def _content_blocks(response: Any) -> list[Any]:
    if response is None:
        return []
    if isinstance(response, dict):
        content = response.get("content")
    else:
        content = getattr(response, "content", None)
    if content is None:
        return []
    if isinstance(content, list):
        return list(content)
    return [content]


def _block_field(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)
