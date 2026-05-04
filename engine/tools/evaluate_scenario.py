"""evaluate_scenario — agentic scenario evaluator (Option C step 2).

Per AGENTIC_ARCHITECTURE_INDEX.md §2.3 (Option C evaluator-optimizer):
critique a `CompiledScenario` against the four-axis rubric loaded
from `engine/prompts/scenario_evaluator.md` and emit an
`EvaluatorVerdict`.

Workers-class per architecture_research.pdf §7.4 — Sonnet 4.6, not
Opus.  The evaluator is critical and not generous; soft-mode failures
are acceptable, over-generous evaluation produces low-quality
manifests.

Annotated `readOnlyHint=True` — the evaluator critiques; it does
not write.  The synthesizer (M15c) is the sole writer.

Cost: ~$0.005 per evaluation with system prompt cached at ttl="1h".
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from engine.schemas_agentic import CompiledScenario, EvaluatorVerdict
from engine.server import engine_server

log = logging.getLogger(__name__)


_EVALUATOR_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "scenario_evaluator.md"
)
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_TOKENS = 1024


@engine_server.tool(annotations={"readOnlyHint": True})
async def evaluate_scenario(
    scenario: CompiledScenario,
    ctx: Any,
) -> EvaluatorVerdict:
    """Critique a CompiledScenario against the four-axis rubric.

    Args:
        scenario:  the compiler's proposal.
        ctx:       FastMCP / MCP-sampling Context.

    Returns:
        Parsed `EvaluatorVerdict` with score, rubric_breakdown, gaps,
        and suggested_fixes.

    Raises:
        ValueError:        when the model's tool_use payload is missing.
        ValidationError:   when the tool_use input fails Pydantic
                           validation (the synthesizer in M15c gets
                           the next pass — soft-mode tolerates this).
    """
    system = _cached_system_block(_load_evaluator_prompt())

    schema = EvaluatorVerdict.model_json_schema()
    emit_tool = {
        "name": "emit_verdict",
        "description": "Emit the rubric verdict for the proposed scenario.",
        "input_schema": schema,
        "strict": True,
    }
    response = await ctx.sample(
        model=_DEFAULT_MODEL,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                "Critique this CompiledScenario against the rubric:\n\n"
                f"{json.dumps(scenario.model_dump(mode='json'), indent=2)}"
            ),
        }],
        tools=[emit_tool],
        tool_choice={"type": "tool", "name": "emit_verdict"},
        max_tokens=_DEFAULT_MAX_TOKENS,
    )
    tool_input = _extract_tool_input(response, "emit_verdict")
    return EvaluatorVerdict.model_validate(tool_input)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_evaluator_prompt() -> str:
    return _EVALUATOR_PROMPT_PATH.read_text()


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
