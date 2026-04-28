"""Thin wrapper around the Claude Agent SDK.

The orchestrator's job is to compose specialists and gate their outputs
through the deliberation contract.  It never writes business data directly.

Defaults documented in the build prompt:
  - model           = claude-opus-4-7
  - max_turns       = 20
  - max_budget_usd  = 2.00
  - fallback_model  = claude-sonnet-4-6
  - permission_mode = "dontAsk"   (NEVER bypassPermissions / acceptEdits — those are unrevocable)
  - thinking        = adaptive
  - betas           = context-management-2025-06-27, task-budgets-2026-03-13
  - prompt cache    = ttl="1h" on every system prompt

This module exposes a small surface — `OrchestratorClient.run(...)` and
`OrchestratorClient.with_subagents(...)` — so the layer servers don't depend
on the SDK's import shape.  When the SDK API drifts, only this file changes.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from infra.telemetry import get_tracer, inject_traceparent_into_env

log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Defaults (build-prompt anchored)
# -----------------------------------------------------------------------------

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_FALLBACK_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_BUDGET_USD = 2.00
DEFAULT_PERMISSION_MODE = "dontAsk"
DEFAULT_THINKING: dict[str, str] = {"type": "adaptive"}
DEFAULT_BETAS: tuple[str, ...] = (
    "context-management-2025-06-27",
    "task-budgets-2026-03-13",
)
DEFAULT_PROMPT_CACHE_TTL = "1h"


@dataclass
class RunOptions:
    """Per-call overrides; missing fields fall back to module defaults."""

    model: str = DEFAULT_MODEL
    fallback_model: str = DEFAULT_FALLBACK_MODEL
    max_turns: int = DEFAULT_MAX_TURNS
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD
    permission_mode: str = DEFAULT_PERMISSION_MODE
    thinking: Mapping[str, Any] = field(default_factory=lambda: dict(DEFAULT_THINKING))
    betas: tuple[str, ...] = DEFAULT_BETAS
    prompt_cache_ttl: str = DEFAULT_PROMPT_CACHE_TTL
    # MCP servers exposed to this run (mapping name -> server config).
    mcp_servers: Mapping[str, Any] = field(default_factory=dict)
    # Tool allowlist; empty means "inherit from agent definition".
    allowed_tools: tuple[str, ...] = ()
    # Subagent registry (AgentDefinition.name -> AgentDefinition).
    subagents: Mapping[str, Any] = field(default_factory=dict)
    # Output / task budget block sent to the SDK as output_config.
    task_budget: Mapping[str, Any] | None = None
    # Hooks attached at run time (PreToolUse, PostToolUse, …).
    hooks: Mapping[str, list[Any]] = field(default_factory=dict)
    # Optional OTel trace context propagation override.
    extra_env: Mapping[str, str] = field(default_factory=dict)


@dataclass
class RunResult:
    """Normalised result shape so callers don't depend on SDK internals."""

    text: str
    raw: Any
    cost_usd: float | None = None
    stop_reason: str | None = None


class OrchestratorClient:
    """Wrapper used by every layer that needs to invoke the SDK.

    Two methods:
      - run(prompt, options) — single one-shot execution.
      - with_subagents(agents) — returns a new client whose default options
        carry the given subagent registry.
    """

    def __init__(self, default_options: RunOptions | None = None) -> None:
        self._defaults = default_options or RunOptions()

    # ---- composition ------------------------------------------------------
    def with_subagents(self, agents: Mapping[str, Any]) -> "OrchestratorClient":
        merged = {**dict(self._defaults.subagents), **dict(agents)}
        new_defaults = RunOptions(
            **{**self._defaults.__dict__, "subagents": merged}
        )
        return OrchestratorClient(new_defaults)

    def with_mcp_servers(self, servers: Mapping[str, Any]) -> "OrchestratorClient":
        merged = {**dict(self._defaults.mcp_servers), **dict(servers)}
        new_defaults = RunOptions(
            **{**self._defaults.__dict__, "mcp_servers": merged}
        )
        return OrchestratorClient(new_defaults)

    # ---- execution --------------------------------------------------------
    async def run(self, prompt: str, options: RunOptions | None = None) -> RunResult:
        opts = options or self._defaults

        # Lazy import so unit tests can patch / skip when SDK isn't installed.
        from claude_agent_sdk import query  # type: ignore

        env = inject_traceparent_into_env(dict(os.environ))
        env.update(opts.extra_env)

        sdk_kwargs = self._build_sdk_kwargs(opts)

        tracer = get_tracer()
        span_cm = tracer.start_as_current_span("orchestrator.run") if tracer else _noop_cm()
        with span_cm:
            try:
                raw = await query(prompt=prompt, **sdk_kwargs)
            except Exception as exc:
                log.exception("orchestrator run failed: %s", exc)
                raise

        return self._normalise(raw)

    # ---- internals --------------------------------------------------------
    def _build_sdk_kwargs(self, opts: RunOptions) -> dict[str, Any]:
        """Translate RunOptions → claude_agent_sdk.query kwargs.

        Kept in one place so SDK API drift is a single-file change.
        """
        kwargs: dict[str, Any] = {
            "model": opts.model,
            "fallback_model": opts.fallback_model,
            "max_turns": opts.max_turns,
            "max_budget_usd": opts.max_budget_usd,
            "permission_mode": opts.permission_mode,
            "thinking": dict(opts.thinking),
            "betas": list(opts.betas),
            "prompt_cache": {"ttl": opts.prompt_cache_ttl},
        }
        if opts.mcp_servers:
            kwargs["mcp_servers"] = dict(opts.mcp_servers)
        if opts.allowed_tools:
            kwargs["allowed_tools"] = list(opts.allowed_tools)
        if opts.subagents:
            kwargs["subagents"] = dict(opts.subagents)
        if opts.task_budget is not None:
            kwargs["output_config"] = {"task_budget": dict(opts.task_budget)}
        if opts.hooks:
            kwargs["hooks"] = {k: list(v) for k, v in opts.hooks.items()}
        return kwargs

    @staticmethod
    def _normalise(raw: Any) -> RunResult:
        """Best-effort extraction of text + cost + stop_reason from SDK output."""
        text = getattr(raw, "text", None) or getattr(raw, "output_text", None) or str(raw)
        cost = getattr(raw, "cost_usd", None) or getattr(raw, "total_cost_usd", None)
        stop = getattr(raw, "stop_reason", None)
        return RunResult(text=text, raw=raw, cost_usd=cost, stop_reason=stop)


# Module-level singleton — most callers just `from orchestrator.client import client`.
client = OrchestratorClient()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

class _noop_cm:
    def __enter__(self) -> None:  # noqa: D401
        return None

    def __exit__(self, *_: Any) -> None:
        return None
