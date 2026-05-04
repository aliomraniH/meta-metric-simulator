"""compile_scenario — agentic scenario compiler.

Per architecture_research.pdf §3.4 + AGENTIC_ARCHITECTURE_INDEX.md
§2.3 (Option C step 1): translate a natural-language hypothetical
into a `CompiledScenario` manifest the deterministic M7 engine can
execute.

The compiler runs on Sonnet 4.6 with strict tool use (`strict:true`)
and adaptive thinking.  It MAY call the deterministic helper tools
(`get_audience_definition`, `lookup_seasonality`) in parallel with
its own thinking — those are read-only and free to invoke.

Annotated `readOnlyHint=True`.  The compiler proposes; the
synthesizer (M15c, `synthesize_scenario`) is the sole writer to the
canonical scenarios table.

Cost: ~$0.005 per compile with the system prompt cached at ttl="1h".
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from engine.schemas_agentic import CompiledScenario
from engine.server import engine_server

log = logging.getLogger(__name__)


_COMPILER_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "scenario_compiler.md"
)
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_TOKENS = 2048


@engine_server.tool(annotations={"readOnlyHint": True})
async def compile_scenario(
    natural_language_intent: str,
    ctx: Any,
    feedback: Optional[list[str]] = None,
) -> CompiledScenario:
    """Translate a natural-language hypothetical into a CompiledScenario.

    Args:
        natural_language_intent:  the user's prompt, verbatim.
        ctx:                       FastMCP / MCP-sampling Context.
        feedback:                  optional list of revision hints from
                                   a prior evaluator pass.  Each hint
                                   is treated as a constraint to
                                   address on the new attempt.

    Returns:
        A parsed `CompiledScenario`.

    Raises:
        ValueError:        when the model's tool_use payload is missing.
        ValidationError:   when the tool_use input fails Pydantic
                           validation (the M15b evaluator + the M15c
                           synthesizer get the next pass).
    """
    system = _cached_system_block(_load_compiler_prompt())

    user_lines: list[str] = [
        "Compile this hypothetical into a CompiledScenario:",
        "",
        natural_language_intent.strip(),
    ]
    if feedback:
        user_lines += [
            "",
            "Previous attempt scored below threshold.  Address every gap:",
        ]
        for i, item in enumerate(feedback, start=1):
            user_lines.append(f"  {i}. {item}")

    schema = CompiledScenario.model_json_schema()
    emit_tool = {
        "name": "emit_compiled_scenario",
        "description": "Emit the compiled scenario manifest in the canonical schema.",
        "input_schema": schema,
        "strict": True,
    }
    response = await ctx.sample(
        model=_DEFAULT_MODEL,
        system=system,
        messages=[{"role": "user", "content": "\n".join(user_lines)}],
        tools=[emit_tool],
        tool_choice={"type": "tool", "name": "emit_compiled_scenario"},
        max_tokens=_DEFAULT_MAX_TOKENS,
    )
    tool_input = _extract_tool_input(response, "emit_compiled_scenario")

    # Backfill the user's intent if the model dropped it; the
    # natural_language_intent field is provenance-load-bearing.
    if not tool_input.get("natural_language_intent"):
        tool_input["natural_language_intent"] = natural_language_intent

    return CompiledScenario.model_validate(tool_input)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_compiler_prompt() -> str:
    return _COMPILER_PROMPT_PATH.read_text()


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
