"""narrate_anomalies — agentic narrator (Sonnet 4.6, soft mode).

Per architecture_research.pdf §3.6 + AGENTIC_ARCHITECTURE_INDEX.md
§2.3 (Option A hook-only).  The narrator reads M14's deterministic
anomaly outputs and produces plain-English hypotheses.

The non-negotiable rule (PDF §3.6): the narrator MUST NOT invent
numeric values.  This tool's prompt instructs that constraint; the
synthesize_insight tool's deterministic invented-number check is
what enforces it mechanically.

Annotated `readOnlyHint=True`.  The synthesizer is the sole writer.

Cost: ~$0.005 per narration with the system prompt cached at ttl="1h".
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from insights.deterministic import Anomaly, z_score_anomalies
from insights.schemas import Narrative
from insights.server import insights_server

log = logging.getLogger(__name__)


_NARRATOR_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "narrator.md"
)
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_TOKENS = 2048
_SIMILAR_THRESHOLD = 4.0  # only fetch similar scenarios for |z| > this


# Type alias for the runtime factory.  FastMCP can't serialise a live
# MetricRuntime, so callers pass a zero-arg factory the tool calls
# inside the request to construct one against the current scenario.
RuntimeFactory = Callable[[], Awaitable[Any]]


@insights_server.tool(
    annotations={"readOnlyHint": True},
    exclude_args=["metric_runtime", "metric_runtime_factory"],
)
async def narrate_anomalies(
    scenario_id: str,
    metric_name: str,
    ctx: Any,
    metric_runtime: Any | None = None,
    metric_runtime_factory: RuntimeFactory | None = None,
) -> Narrative:
    """Narrate the deterministic z-score anomalies for one (scenario, metric).

    Args:
        scenario_id:               canonical scenario id.
        metric_name:               metric defined under metrics/definitions/.
        ctx:                       FastMCP / MCP-sampling Context.
        metric_runtime:            an already-constructed MetricRuntime
                                   (test seam).
        metric_runtime_factory:    a zero-arg async callable returning a
                                   MetricRuntime — used in production
                                   where FastMCP can't serialise the
                                   live runtime through tool args.

    Returns:
        A `Narrative`.  Empty `hypotheses` list when there are no
        anomalies — silence beats forced narration.

    Raises:
        ValueError: when neither metric_runtime nor metric_runtime_factory
                    is provided.
    """
    if metric_runtime is None and metric_runtime_factory is None:
        raise ValueError(
            "narrate_anomalies requires either metric_runtime or metric_runtime_factory"
        )
    runtime = metric_runtime if metric_runtime is not None else await metric_runtime_factory()  # type: ignore[misc]

    anomalies = await z_score_anomalies(
        scenario_id=scenario_id,
        metric_name=metric_name,
        metric_runtime=runtime,
    )

    if not anomalies:
        # Silence beats forced narration.  Confidence 1.0 because
        # "no anomaly" is a strong, unambiguous statement.
        return Narrative(
            scenario_id=scenario_id,
            metric_name=metric_name,
            hypotheses=[],
            overall_confidence=1.0,
        )

    system = _cached_system_block(_load_narrator_prompt())

    user_payload = {
        "scenario_id":  scenario_id,
        "metric_name":  metric_name,
        "anomalies": [_anomaly_to_payload(a) for a in anomalies],
        # Note: similar-scenario context can be appended by callers
        # via ctx.sample's parallel tool-use path; we don't fetch it
        # here to keep narrate_anomalies callable without a Voyage
        # API key for unit tests.
    }

    schema = Narrative.model_json_schema()
    emit_tool = {
        "name": "emit_narrative",
        "description": "Emit the narrator's hypotheses as a Narrative.",
        "input_schema": schema,
        "strict": True,
    }
    response = await ctx.sample(
        model=_DEFAULT_MODEL,
        system=system,
        messages=[{
            "role": "user",
            "content": json.dumps(user_payload, indent=2),
        }],
        tools=[emit_tool],
        tool_choice={"type": "tool", "name": "emit_narrative"},
        max_tokens=_DEFAULT_MAX_TOKENS,
    )
    tool_input = _extract_tool_input(response, "emit_narrative")
    # Backfill scenario_id / metric_name if the model dropped them.
    tool_input.setdefault("scenario_id", scenario_id)
    tool_input.setdefault("metric_name", metric_name)
    return Narrative.model_validate(tool_input)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _anomaly_to_payload(a: Anomaly) -> dict[str, Any]:
    """Serialise an Anomaly for the narrator's user-message context.

    Includes a stable `ref` key so the narrator can populate
    Hypothesis.anomaly_ref deterministically and the synthesize_insight
    missing-anomaly-ref check works downstream.
    """
    return {
        "ref":             f"{a.metric_name}:{a.tick_day}",
        "metric_name":     a.metric_name,
        "tick_day":        a.tick_day,
        "observed_value":  a.observed_value,
        "expected_value":  a.expected_value,
        "z_score":         a.z_score,
        "direction":       a.direction,
    }


def _load_narrator_prompt() -> str:
    return _NARRATOR_PROMPT_PATH.read_text()


def _cached_system_block(prompt: str) -> list[dict[str, Any]]:
    """System prompt block with ttl=1h cache_control per PDF §6.1."""
    return [{
        "type": "text",
        "text": prompt,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }]


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
        f"response did not contain a tool_use block named {tool_name!r}"
    )
