"""extract_metric — the baseline-extractor tool, two-call pattern.

Per `docs/AGENTIC_ARCHITECTURE_INDEX.md` §5 anti-pattern 7.3 (and the
underlying architecture-research PDF §7.3), Citations API + Structured
Outputs **cannot combine in one call** — the API returns 400 and the
extraction silently fails.  This tool implements the canonical
two-call sequence:

  Call 1 — Citations: gather quoted evidence with page anchors.
                       No strict tool use; document content block with
                       `citations.enabled=True`.
  Call 2 — Strict tool use: format the gathered evidence as an
                       ExtractedMetric record.  No citations; tools=[
                       emit_metric] with `strict:true` and tool_choice.

The tool is annotated `readOnlyHint=True` because it does not write
to canonical state — it returns an `ExtractedMetric` for the
synthesizer (M12c-ii) to judge.  The Layer-isolation hook treats this
as a non-writer; the synthesizer is the sole writer.

Cost: ~$0.005 per extraction with the system-prompt cached at
ttl="1h".  Per PDF §7.4, both calls run on Sonnet 4.6 (workers don't
need Opus).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from baselines.schemas import ExtractedMetric
from baselines.server import baselines_server

log = logging.getLogger(__name__)


_EXTRACTOR_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "extractor.md"
)
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_TOKENS = 1024
_DEFAULT_CONFIDENCE_FLOOR = 0.85


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------

@baselines_server.tool(annotations={"readOnlyHint": True})
async def extract_metric(
    file_id: str,
    metric_id: str,
    period: str,
    ctx: Any,
) -> ExtractedMetric:
    """Extract a numeric metric using the two-call pattern.

    Args:
        file_id:    Anthropic Files API file id of the source document.
        metric_id:  canonical metric name (e.g. "dap_billion").
        period:     period the value covers (e.g. "FY 2025", "Q4 2025").
        ctx:        the FastMCP / MCP-sampling Context.  ctx.sample
                    forwards completions to the orchestrator's Claude.

    Returns:
        ExtractedMetric with citation-anchored source.

    Raises:
        ValueError:        when Call 1 returned no citations.
        ValidationError:   when the Call-2 tool_use input fails Pydantic
                           validation twice (initial + retry).
    """
    system = _cached_system_block(_load_extractor_prompt())

    # ---- Call 1: Citations gathering ---------------------------------------
    citations_messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "file", "file_id": file_id},
                    "citations": {"enabled": True},
                },
                {
                    "type": "text",
                    "text": (
                        f"Extract {metric_id} for {period} from the attached "
                        "document. Cite the page and quote the exact text."
                    ),
                },
            ],
        }
    ]
    citations_response = await ctx.sample(
        model=_DEFAULT_MODEL,
        system=system,
        messages=citations_messages,
        max_tokens=_DEFAULT_MAX_TOKENS,
    )
    citations = _extract_citations(citations_response)
    if not citations:
        raise ValueError(
            "Extraction lacks citations — required by L4 strict mode. "
            f"metric_id={metric_id!r} period={period!r} file_id={file_id!r}"
        )

    citations_text = _extract_text(citations_response)

    # ---- Call 2: Strict tool use formatting --------------------------------
    schema = ExtractedMetric.model_json_schema()
    emit_tool = {
        "name": "emit_metric",
        "description": "Emit the extracted metric in the canonical schema.",
        "input_schema": schema,
        "strict": True,
    }
    tool_messages = [
        {
            "role": "user",
            "content": (
                f"Format this extraction as ExtractedMetric:\n\n"
                f"{citations_text}\n\n"
                f"Citations: {json.dumps(citations)}"
            ),
        }
    ]
    tool_response = await ctx.sample(
        model=_DEFAULT_MODEL,
        system=system,
        messages=tool_messages,
        tools=[emit_tool],
        tool_choice={"type": "tool", "name": "emit_metric"},
        max_tokens=_DEFAULT_MAX_TOKENS,
    )
    tool_input = _extract_tool_input(tool_response, "emit_metric")

    # ---- Construct + retry-once on validation error ------------------------
    try:
        result = ExtractedMetric.model_validate(tool_input)
    except ValidationError as exc:
        log.info("extract_metric initial tool_use validation failed; retrying once: %s", exc)
        retry_messages = tool_messages + [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "emit_metric", "input": tool_input}
                ],
            },
            {
                "role": "user",
                "content": (
                    "That output failed Pydantic validation against the "
                    f"ExtractedMetric schema:\n{exc!s}\n\n"
                    "Re-emit with the corrections; preserve the citations."
                ),
            },
        ]
        retry_response = await ctx.sample(
            model=_DEFAULT_MODEL,
            system=system,
            messages=retry_messages,
            tools=[emit_tool],
            tool_choice={"type": "tool", "name": "emit_metric"},
            max_tokens=_DEFAULT_MAX_TOKENS,
        )
        tool_input = _extract_tool_input(retry_response, "emit_metric")
        result = ExtractedMetric.model_validate(tool_input)

    # ---- Augment source from citations when fields are blank ---------------
    cit0 = citations[0] if citations else {}
    cit_url = _str_or_empty(cit0.get("source"))
    cit_quoted = _str_or_empty(cit0.get("cited_text") or cit0.get("quoted_text"))
    cit_page = cit0.get("page") or cit0.get("start_page_number")

    src = result.source
    augmented_source = src.model_copy(update={
        "url":         src.url or cit_url,
        "quoted_text": src.quoted_text or cit_quoted,
        "page":        src.page if src.page is not None else (
            int(cit_page) if isinstance(cit_page, (int, float)) else src.page
        ),
    })
    if augmented_source != src:
        result = result.model_copy(update={"source": augmented_source})

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_extractor_prompt() -> str:
    return _EXTRACTOR_PROMPT_PATH.read_text()


def _cached_system_block(prompt: str) -> list[dict[str, Any]]:
    """System prompt block with ttl=1h cache_control per PDF §6.1."""
    return [
        {
            "type": "text",
            "text": prompt,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def _content_blocks(response: Any) -> list[Any]:
    """Pull the content blocks from a sample response.

    Tolerates both dict-shaped (`response["content"]`) and object-shaped
    (`response.content`) results so tests can use whichever is more
    convenient.  Returns [] when neither shape is present.
    """
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
    # Some shapes return a single block; wrap it for uniform iteration.
    return [content]


def _block_field(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _extract_citations(response: Any) -> list[dict[str, Any]]:
    """Flatten citations attached to text blocks into a single list."""
    out: list[dict[str, Any]] = []
    for block in _content_blocks(response):
        if _block_field(block, "type") != "text":
            continue
        cits = _block_field(block, "citations") or []
        for c in cits:
            if isinstance(c, dict):
                out.append(c)
            else:
                # Object-style — extract the documented fields.
                out.append({
                    "source": getattr(c, "source", None),
                    "cited_text": getattr(c, "cited_text", None),
                    "page": getattr(c, "page", None) or getattr(c, "start_page_number", None),
                })
    return out


def _extract_text(response: Any) -> str:
    """Concatenate text from all text blocks in the response."""
    parts: list[str] = []
    for block in _content_blocks(response):
        if _block_field(block, "type") == "text":
            t = _block_field(block, "text", "")
            if t:
                parts.append(str(t))
    return "\n".join(parts)


def _extract_tool_input(response: Any, tool_name: str) -> dict[str, Any]:
    """Find the tool_use block with the given tool name and return its input."""
    for block in _content_blocks(response):
        if _block_field(block, "type") != "tool_use":
            continue
        if _block_field(block, "name") != tool_name:
            continue
        ti = _block_field(block, "input", {})
        if isinstance(ti, dict):
            return ti
        return {}
    raise ValueError(
        f"response did not contain a tool_use block named {tool_name!r}; "
        "the strict tool-use call did not produce the expected output"
    )


def _str_or_empty(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, dict):
        # Anthropic citations sometimes wrap source as {"type": "url", "url": "..."}.
        return str(x.get("url") or x.get("file_id") or "")
    return str(x)
