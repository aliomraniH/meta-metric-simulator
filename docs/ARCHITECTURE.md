# Reels AT Simulator — Architecture

## Summary

```
Front door:                 FastMCP server (Streamable HTTP) on Replit Reserved VM
                            Composition: mount() — never chained Client calls
                            Stateful mode (sampling/elicitation/roots require it)
                            Mcp-Session-Id + Redis-backed EventStore for resume
                            Postgres or Replit DB for state — not local SQLite

Orchestrator:               Claude Agent SDK (Python), claude-opus-4-7
                            Programmatic subagents per agentic layer
                            Hooks: PreToolUse sanitize gate, PostToolUse synthesize rewrite
                            Workers: Sonnet 4.6 / Haiku 4.5; Opus only for orchestrator + final synthesis
                            CLAUDE_CODE_FORK_SUBAGENT=1, prompt cache ttl="1h"

Layers:
  Layer 10 — Interview Tools (LLM-gated, calibration-locked)
  Layer  9 — Calibration (deterministic; gates Layer 10)
  Layer  8 — Insights        ← AGENTIC, soft mode
  Layer  7 — Metrics DSL     (deterministic SQL)
  Layer  6 — Engine          ← AGENTIC for scenario synthesis only, evaluator-optimizer
  Layer  5 — Curation        ← AGENTIC, strict mode, constitutional critique-revise
  Layer  4 — Baselines       ← AGENTIC, strict mode, sanitizer-as-tool / synthesizer-as-judge
  Layer  3 — Parameters      (per-algorithm YAML, agent-free)
  Layer  2 — Algorithms      (pure functions, agent-free)
  Layer  1 — Event substrate (raw event log, agent-free)
  Layer  0 — Infra           (FastMCP, Redis, Postgres, OAuth, telemetry)

Deliberation pattern (per agentic layer):
  Specialist tool → ctx.sample() to orchestrator's Claude → structured proposal
       ↓
  Sanitizer tools (parallel @mcp.tool, code or Haiku) → flags[]
       ↓
  Synthesizer subagent (single LLM-judge, 0.0–1.0 rubric) → decision + confidence
       ↓
  Strict mode: PreToolUse hook denies if severity≥high or confidence<0.85
                → ctx.elicit() to human for diff-confirm
  Soft mode:   PostToolUse rewrites updatedMCPToolOutput with
                {disputed, confidence, disputed_by}
       ↓
  Single-writer commit (synthesizer is the only path that writes)
```

## Agentic vs deterministic layers

| Layer | Name             | Agentic? | Mode       | Rationale                                                                 |
|-------|------------------|----------|------------|---------------------------------------------------------------------------|
| 0     | Infra            | No       | —          | Plumbing: FastMCP, Redis, Postgres, OAuth, telemetry.                     |
| 1     | Event substrate  | No       | —          | Append-only log; correctness comes from schema, not judgment.             |
| 2     | Algorithms       | No       | —          | Pure functions. Determinism is non-negotiable.                            |
| 3     | Parameters       | No       | —          | Static YAML with provenance. No reasoning needed at read time.            |
| 4     | Baselines        | **Yes**  | **Strict** | External-fact ingestion — hallucination risk is high; humans gate writes. |
| 5     | Curation         | **Yes**  | **Strict** | Source-of-truth refresh — needs constitutional cite-or-flag rule.         |
| 6     | Engine compiler  | **Yes**  | **Soft**   | Scenario synthesis only; deterministic engine itself stays agent-free.    |
| 7     | Metrics DSL      | No       | —          | Pure SQL templates. Composability is the point.                           |
| 8     | Insights         | **Yes**  | **Soft**   | Narration over deterministic detector output.                             |
| 9     | Calibration      | No       | —          | Inequality tests with explicit pass/fail. Gates Phase 2.                  |
| 10    | Interview tools  | No       | —          | LLM-driven, but gated by calibration; not part of the deliberation loop.  |

Strict mode means: PreToolUse hook denies any write below the confidence floor
or above the severity block threshold; the synthesizer must present a diff to
a human via `ctx.elicit()` before commit.

Soft mode means: writes are allowed, but the PostToolUse hook annotates them
with `{disputed, confidence, disputed_by}` so downstream consumers can see
that the synthesizer was uncertain.

## Composition: mount, never chained Client

Layer servers (l4, l5, l6, l8) are FastMCP instances mounted into the
front-door FastMCP at fixed namespace prefixes.  Mount preserves the
bidirectional sampling channel — a tool inside `l4` can call `ctx.sample()`
and the request reaches the orchestrator's Claude.  Chained Client calls
(server-A as a Client of server-B) break that channel.

## Replit-specific constraints

- Streamable HTTP MUST be **stateful** (Mcp-Session-Id).  Stateless mode
  disables sampling, elicitation, and roots — features the agentic layers
  rely on.
- Resumability is implemented by a **Redis-backed EventStore** keyed by
  session id; clients reconnect with `Last-Event-ID` and the server replays.
- State lives in **Postgres** (or Replit DB key-value as a fallback).  Local
  SQLite is forbidden because Replit Deployment redeploys do not preserve
  filesystem writes.
- `.replit` sets `CLAUDE_CODE_FORK_SUBAGENT=1` for ~90% input-token savings
  on subagent fan-out.
- Every LLM call wrapper sets `prompt_caching` with `ttl="1h"` explicitly —
  the default was silently dropped to 5 minutes in March 2026.

## Trust boundaries and the single-writer rule

For every agentic layer, the **synthesizer tool is the sole writer** to the
canonical artifact (baselines yaml, scenarios row, insights row, etc).  All
other tools either read or produce ephemeral observations.  The PreToolUse
hook enforces this by denying writes from any other tool.

This single-writer rule is what makes auditability tractable: every
canonical change has exactly one synthesis row, which references the
observations that fed into it.  Replaying a decision is a join.

## OAuth and Resource Indicators

Each agentic layer is its own RFC 8707 resource with its own scopes.  Tokens
are issued per-resource and MUST NOT be passed through between layer servers.
Cross-layer access goes through the orchestrator, which performs an RFC 8693
token exchange — never reuses an inbound token.
