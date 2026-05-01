"""diff_proposal — propose a sources_registry.yaml diff via the two-call pattern.

Per architecture_research.pdf §7.3 (and the AGENTIC_ARCHITECTURE_INDEX.md
§5 anti-pattern table): Citations API + Structured Outputs cannot
combine in one call (the API returns 400).  This tool runs the
canonical two-call sequence:

  Call 1 — Citations: web_search + web_fetch with `citations.enabled=True`
                       to gather quoted evidence anchored to URLs.
  Call 2 — Strict tool use: `tools=[emit_diff_proposal]`,
                       `strict:true`, `tool_choice` locked.  Formats
                       the gathered evidence into a `DiffProposal`
                       record.  No citations.

Annotated `readOnlyHint=True` because the synthesizer (M13b
`synthesize_diff`) is the sole writer to `curation/sources_registry.yaml`.
This tool only proposes; the synthesizer asks the human via
`ctx.elicit` for diff-confirm before any write reaches disk.

Cost: ~$0.01 per proposal with the system prompt cached at ttl="1h".
Sonnet 4.6 (PDF §7.4 — workers don't need Opus).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from curation.schemas import DiffProposal
from curation.server import curation_server
from curation.tools.web_fetch import web_fetch
from curation.tools.web_search import web_search

log = logging.getLogger(__name__)


_REFRESHER_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "refresher.md"
)
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_TOKENS = 2048
_DEFAULT_REGISTRY_PATH = Path("curation/sources_registry.yaml")


@curation_server.tool(annotations={"readOnlyHint": True})
async def diff_proposal(
    registry_entry_id: str,
    field: str,
    ctx: Any,
) -> DiffProposal:
    """Propose a diff for one field on one sources_registry entry.

    Args:
        registry_entry_id:  source key in sources_registry.yaml.
        field:              field being diffed (url, accessed_date, ...).
        ctx:                FastMCP / MCP-sampling Context.

    Returns:
        DiffProposal with cited_evidence and confidence.

    Raises:
        ValueError:        when no allowed-domain hits were found.
        ValidationError:   when Call-2 tool_use input fails Pydantic
                           validation (no retry — the constitutional
                           critic gets the next pass).
    """
    system = _cached_system_block(_load_refresher_prompt())

    # ---- Call 1: gather cited evidence ------------------------------------
    search_query = f"{registry_entry_id} {field} 2026 primary source"
    hits = await web_search(query=search_query, ctx=ctx)
    if not hits:
        raise ValueError(
            f"web_search returned no allowed-domain hits for "
            f"registry_entry_id={registry_entry_id!r} field={field!r}"
        )

    # Fetch the top 1-2 hits with citations enabled (cost-disciplined).
    fetched: list[dict[str, Any]] = []
    for h in hits[:2]:
        try:
            r = await web_fetch(url=h["url"], ctx=ctx)
            fetched.append({"url": h["url"], **r})
        except Exception as exc:  # noqa: BLE001
            log.warning("web_fetch failed for %s: %s", h["url"], exc)

    aggregated_evidence: list[dict[str, Any]] = []
    aggregated_text_parts: list[str] = []
    for f in fetched:
        if f.get("content_text"):
            aggregated_text_parts.append(str(f["content_text"]))
        for c in (f.get("citations") or []):
            if isinstance(c, dict):
                aggregated_evidence.append({
                    "url": c.get("source_url") or f["url"],
                    "page": c.get("page"),
                    "quoted_text": c.get("cited_text", ""),
                })

    # ---- Call 2: strict tool use formats the DiffProposal -----------------
    schema = DiffProposal.model_json_schema()
    emit_tool = {
        "name": "emit_diff_proposal",
        "description": "Emit the curated diff proposal in the canonical schema.",
        "input_schema": schema,
        "strict": True,
    }
    tool_messages = [
        {
            "role": "user",
            "content": (
                "Format the following evidence as a DiffProposal "
                f"for registry_entry_id={registry_entry_id!r}, field={field!r}.\n\n"
                "Evidence text:\n"
                + "\n---\n".join(aggregated_text_parts)
                + "\n\nCitations: "
                + json.dumps(aggregated_evidence)
            ),
        }
    ]
    tool_response = await ctx.sample(
        model=_DEFAULT_MODEL,
        system=system,
        messages=tool_messages,
        tools=[emit_tool],
        tool_choice={"type": "tool", "name": "emit_diff_proposal"},
        max_tokens=_DEFAULT_MAX_TOKENS,
    )

    tool_input = _extract_tool_input(tool_response, "emit_diff_proposal")
    # Backfill the cited_evidence from Call 1 when the agent forgot to.
    if not tool_input.get("cited_evidence") and aggregated_evidence:
        tool_input["cited_evidence"] = aggregated_evidence
    if not tool_input.get("registry_entry_id"):
        tool_input["registry_entry_id"] = registry_entry_id
    if not tool_input.get("field"):
        tool_input["field"] = field

    try:
        return DiffProposal.model_validate(tool_input)
    except ValidationError:
        # Re-raise — the constitutional critic + synthesizer get the next pass.
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_refresher_prompt() -> str:
    return _REFRESHER_PROMPT_PATH.read_text()


def _cached_system_block(prompt: str) -> list[dict[str, Any]]:
    """System prompt block with ttl=1h cache_control per PDF §6.1."""
    return [
        {
            "type": "text",
            "text": prompt,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def _extract_tool_input(response: Any, tool_name: str) -> dict[str, Any]:
    for block in _content_blocks(response):
        if _block_field(block, "type") != "tool_use":
            continue
        if _block_field(block, "name") != tool_name:
            continue
        ti = _block_field(block, "input", {})
        if isinstance(ti, dict):
            return dict(ti)
        return {}
    raise ValueError(
        f"response did not contain a tool_use block named {tool_name!r}; "
        "the strict tool-use call did not produce the expected output"
    )


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
