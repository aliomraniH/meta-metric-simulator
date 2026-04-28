"""Programmatic AgentDefinition registry for the orchestrator.

Every agentic role in the system has exactly one definition here.  The
build prompt enumerates the nine roles below and pins each one's worker
model.  Opus is reserved for the orchestrator and the synthesizer
(single LLM-judge); workers are Sonnet 4.6 or Haiku 4.5.

Permission-mode rule (load-bearing): NEVER set `bypassPermissions` or
`acceptEdits` on any AgentDefinition.  Those are inherited by subagents
and are unrevocable once set.  Use a tight `allowed_tools` allowlist
plus the PreToolUse hook (orchestrator/hooks.py) for write gating.

Prompt files referenced here are created by later milestones; the
registry simply records the path so tooling that wants to lint
"every agent has a prompt file" can do so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


# Model pins — Opus only for orchestrator + synthesizer; workers are cheaper.
MODEL_OPUS = "claude-opus-4-7"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class AgentDefinition:
    """One agentic role.  Frozen so the registry is read-only at runtime."""

    name: str
    role: str  # human-readable description
    model: str
    prompt_path: str  # repo-relative path to the system prompt file
    mcp_servers: tuple[str, ...] = ()  # which mounted layer servers it sees
    allowed_tools: tuple[str, ...] = ()  # tight allowlist; empty = none
    thinking_budget: int | None = None  # tokens for extended thinking
    notes: str = ""

    def __post_init__(self) -> None:
        # Prevent the two unrevocable permissions from sneaking in via notes
        # or future extensions — assertion stays even if more fields appear.
        forbidden = {"bypassPermissions", "acceptEdits"}
        for tool in self.allowed_tools:
            if tool in forbidden:
                raise ValueError(
                    f"AgentDefinition {self.name!r}: '{tool}' is forbidden "
                    "(unrevocable; use the PreToolUse hook for write gating)"
                )


# -----------------------------------------------------------------------------
# Registry — one entry per agentic role from the build prompt
# -----------------------------------------------------------------------------

AGENTS: Mapping[str, AgentDefinition] = {
    "baseline-extractor": AgentDefinition(
        name="baseline-extractor",
        role="Extracts ExtractedMetric records with citations from uploaded PDFs.",
        model=MODEL_SONNET,
        prompt_path="baselines/prompts/extractor.md",
        mcp_servers=("l4",),
        allowed_tools=("l4_extract_metric",),
        notes=(
            "Strict tool use with strict:true. Citations API enabled. "
            "NEVER combine structured outputs and citations in one call (400)."
        ),
    ),
    "baseline-reconciler": AgentDefinition(
        name="baseline-reconciler",
        role="Reconciles overlapping ExtractedMetric values; ranks by source trust tier.",
        model=MODEL_OPUS,
        prompt_path="baselines/prompts/reconciler.md",
        mcp_servers=("l4",),
        allowed_tools=("l4_extract_metric",),
        thinking_budget=16000,
        notes="Persists losing sources too — never silent dedup.",
    ),
    "curation-refresher": AgentDefinition(
        name="curation-refresher",
        role="Refreshes sources_registry by web search + cited fetch.",
        model=MODEL_SONNET,
        prompt_path="curation/prompts/refresher.md",
        mcp_servers=("l5",),
        allowed_tools=("l5_diff_proposal", "l5_web_search", "l5_web_fetch"),
        notes=(
            "Constitutional critique-revise (Option D). Strict mode — "
            "never auto-applies; synthesize_diff escalates to ctx.elicit."
        ),
    ),
    "scenario-compiler": AgentDefinition(
        name="scenario-compiler",
        role="Compiles natural-language hypotheticals into perturbation manifests.",
        model=MODEL_SONNET,
        prompt_path="engine/prompts/scenario_compiler.md",
        mcp_servers=("l6",),
        allowed_tools=(
            "l6_compile_scenario",
            "l6_get_audience_definition",
            "l6_lookup_seasonality",
        ),
        notes="Structured outputs beta with grammar-constrained decoding.",
    ),
    "scenario-evaluator": AgentDefinition(
        name="scenario-evaluator",
        role="Critiques compiled manifests against a 4-axis rubric.",
        model=MODEL_SONNET,
        prompt_path="engine/prompts/scenario_evaluator.md",
        mcp_servers=("l6",),
        allowed_tools=("l6_evaluate_scenario",),
        notes="Evaluator half of evaluator-optimizer; SDK Stop hook bounds N=2.",
    ),
    "insight-narrator": AgentDefinition(
        name="insight-narrator",
        role="Narrates deterministic anomaly detector output in plain English.",
        model=MODEL_SONNET,
        prompt_path="insights/prompts/narrator.md",
        mcp_servers=("l8",),
        allowed_tools=("l8_narrate_anomalies", "l8_find_similar_scenarios"),
        notes="MUST NOT invent numeric values — narrate detector output only.",
    ),
    "synthesizer": AgentDefinition(
        name="synthesizer",
        role="Single LLM-judge for all strict-mode commits across layers 4/5/6/8.",
        model=MODEL_OPUS,
        prompt_path="orchestrator/prompts/synthesizer.md",
        mcp_servers=("l4", "l5", "l6", "l8"),
        allowed_tools=(
            "l4_synthesize_baseline",
            "l5_synthesize_diff",
            "l6_synthesize_scenario",
            "l8_synthesize_insight",
        ),
        thinking_budget=8000,
        notes=(
            "SOLE WRITER for canonical artifacts.  Returns SynthesizerDecision "
            "{accept|reject|escalate, confidence, rationale}.  Strict-mode "
            "escalations route to ctx.elicit() for human diff-confirm."
        ),
    ),
    "sanitizer-policy": AgentDefinition(
        name="sanitizer-policy",
        role="Policy-side sanitizer (citations present, primary-source-required, etc).",
        model=MODEL_HAIKU,
        prompt_path="orchestrator/prompts/sanitizer_policy.md",
        mcp_servers=("l4", "l5"),
        allowed_tools=(),  # read-only sanitizer; no tool calls
        notes="Code-based where possible; LLM only for fuzzy policy checks.",
    ),
    "sanitizer-schema": AgentDefinition(
        name="sanitizer-schema",
        role="Schema-side sanitizer (range checks, type coherence).",
        model=MODEL_HAIKU,
        prompt_path="orchestrator/prompts/sanitizer_schema.md",
        mcp_servers=("l4", "l5", "l6", "l8"),
        allowed_tools=(),
        notes="Almost entirely deterministic; Haiku used only for ambiguous cases.",
    ),
}


def get(name: str) -> AgentDefinition:
    if name not in AGENTS:
        raise KeyError(f"unknown agent: {name!r}")
    return AGENTS[name]


def by_layer(layer: str) -> tuple[AgentDefinition, ...]:
    """All agents that may operate in the given layer namespace (l4, l5, l6, l8)."""
    return tuple(a for a in AGENTS.values() if layer in a.mcp_servers)


def to_sdk_payload(name: str) -> dict[str, object]:
    """Build the dict shape the Claude Agent SDK expects for an agent.

    Kept minimal — the SDK's exact field names may shift; this is the one
    function to update.  Note we do NOT emit permission_mode, ever.
    """
    a = get(name)
    payload: dict[str, object] = {
        "name": a.name,
        "model": a.model,
        "prompt_path": a.prompt_path,
        "mcp_servers": list(a.mcp_servers),
        "allowed_tools": list(a.allowed_tools),
    }
    if a.thinking_budget is not None:
        payload["thinking"] = {"type": "enabled", "budget_tokens": a.thinking_budget}
    return payload
