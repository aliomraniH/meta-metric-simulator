"""web_fetch — primary-source fetch with citations.

Wraps the Anthropic web_fetch server-side tool with
`citations.enabled=True` and a content cap.  Per
architecture_research.pdf §7.3 this is a citations-only call — the
strict-tool-use call to format a `DiffProposal` happens separately
in `diff_proposal.py`.  Combining citations + structured outputs in
one request returns 400 from the API.

Annotated `readOnlyHint=True`.  The synthesizer (M13b) is what
writes; this tool only returns the fetched content + cited spans.
"""
from __future__ import annotations

import logging
from typing import Any

from curation.server import curation_server

log = logging.getLogger(__name__)


_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_CONTENT_TOKENS = 60000
_DEFAULT_MAX_TOKENS = 2048


@curation_server.tool(annotations={"readOnlyHint": True})
async def web_fetch(url: str, ctx: Any) -> dict[str, Any]:
    """Fetch a primary source with citations enabled.

    Args:
        url:  the page to fetch.  Must be in the allowed-domain set
              (web_search constrains discovery; this just fetches
              what was found).
        ctx:  FastMCP / MCP-sampling Context.

    Returns:
        Dict with two keys:
          `content_text`: concatenated text of all returned text blocks.
          `citations`: list of `{cited_text, source_url, page}` dicts
                       extracted from the citation metadata on those
                       text blocks.
    """
    response = await ctx.sample(
        model=_DEFAULT_MODEL,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Fetch and summarise the following primary source: {url}\n\n"
                    "Quote any numeric facts verbatim with page anchors."
                ),
            }
        ],
        tools=[{
            "type": "web_fetch_20250910",
            "name": "web_fetch",
            "max_content_tokens": _DEFAULT_MAX_CONTENT_TOKENS,
            "citations": {"enabled": True},
        }],
        max_tokens=_DEFAULT_MAX_TOKENS,
    )
    text = _extract_text(response)
    citations = _extract_citations(response, fallback_url=url)
    return {"content_text": text, "citations": citations}


def _extract_text(response: Any) -> str:
    parts: list[str] = []
    for block in _content_blocks(response):
        if _block_field(block, "type") == "text":
            t = _block_field(block, "text", "")
            if t:
                parts.append(str(t))
    return "\n".join(parts)


def _extract_citations(response: Any, *, fallback_url: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in _content_blocks(response):
        if _block_field(block, "type") != "text":
            continue
        cits = _block_field(block, "citations") or []
        for c in cits:
            if isinstance(c, dict):
                out.append({
                    "cited_text": c.get("cited_text", ""),
                    "source_url": c.get("source") or c.get("url") or fallback_url,
                    "page": c.get("page") or c.get("start_page_number"),
                })
            else:
                out.append({
                    "cited_text": getattr(c, "cited_text", ""),
                    "source_url": (
                        getattr(c, "source", None)
                        or getattr(c, "url", None)
                        or fallback_url
                    ),
                    "page": getattr(c, "page", None) or getattr(c, "start_page_number", None),
                })
    return out


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
