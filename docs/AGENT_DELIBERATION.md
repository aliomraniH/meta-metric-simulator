# Agent Deliberation Contract

This document specifies how every agentic layer in the simulator gates its
writes.  The pattern is the same shape across Layer 4 (baselines), Layer 5
(curation), Layer 6 (scenario compiler) and Layer 8 (insight narrator).
Only the **mode** and **thresholds** differ.

## The pattern in one diagram

```
Specialist tool (extractor / refresher / compiler / narrator)
        │
        │  ctx.sample()  ──────────────►  Orchestrator's Claude
        │  ◄──────────────  structured proposal (typed payload)
        ▼
┌───────────────────────────────────────────────────────────┐
│  Sanitizer tools  (parallel @mcp.tool, code or Haiku)     │
│   ─ schema sanitizer (range, type, enum coherence)        │
│   ─ policy sanitizer (cite-or-flag, primary-source rule)  │
│   ─ source-tier sanitizer (SEC > earnings > analyst …)    │
│   ─ … layer-specific extras                                │
│  Each emits SanitizerFlag[] = {kind, severity, evidence}.  │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  Synthesizer subagent (single LLM-judge, Opus 4.7)        │
│  Reads proposal + all sanitizer flags.                    │
│  Returns SynthesizerDecision:                             │
│    {decision, confidence ∈ [0,1], rationale,              │
│     accepted_fixes[], unresolved_disputes[]}              │
└───────────────────────────────────────────────────────────┘
        │
        ▼
   ┌──────────────────────────┴──────────────────────────┐
   │ STRICT MODE                       │ SOFT MODE       │
   │  PreToolUse hook denies if:       │  PostToolUse    │
   │   • confidence < floor            │  rewrites       │
   │   • severity ≥ block_threshold    │  updatedMCP     │
   │  On deny → ctx.elicit() to human  │  ToolOutput     │
   │  for diff-confirm before commit.  │  with           │
   │                                   │  {disputed,     │
   │                                   │   confidence,   │
   │                                   │   disputed_by}  │
   └──────────────────────────┬────────┴──────────────────┘
        │
        ▼
  Single-writer commit
  (synthesize_* tool is the SOLE path that writes the canonical artifact)
```

The single-writer rule is enforced by the PreToolUse hook in
`orchestrator/hooks.py`: any tool whose name contains "synthesize" is the
only path through which writes can occur.  All other tools are read-only or
produce ephemeral observations.

## The two modes

### Strict mode — Layers 4, 5

Used where hallucinated values would corrupt the source of truth.

- `confidence_floor = 0.85`
- `severity_block_threshold = "high"`
- PreToolUse hook **denies** writes that fail either gate.
- On deny, the synthesizer escalates via `ctx.elicit()` to a human, who
  sees a diff and confirms or rejects.
- Layer 4 uses **Option B** (sanitizer-as-tool / synthesizer-as-judge).
- Layer 5 uses **Option D** (constitutional critique-revise).

### Soft mode — Layers 6, 8

Used where exploratory output is welcome but downstream consumers need to
know when the synthesizer was uncertain.

- `confidence_floor = 0.70` (Layer 6) / `0.60` (Layer 8)
- `severity_block_threshold = "critical"` — only critical flags actually block
- PostToolUse hook **annotates** the result with `{disputed, confidence,
  disputed_by, dispute_reasons[]}`.  Writes still happen.
- Layer 6 uses **Option C** (evaluator-optimizer, N=2).
- Layer 8 uses **Option A** (hook-only gate; narrator never invents numbers).

## The four architecture options

Each agentic layer picks one.  The differences are in *where the LLM judgment
lives* and *what the failure mode is*.

### Option A — Hook-only gate (Layer 8 narrator)

The simplest pattern.  No sanitizer subagents and no synthesizer subagent;
all judgment lives in code.  The PostToolUse hook inspects the tool's
output, computes `disputed/confidence` deterministically, and rewrites.

Use when: the tool is itself low-risk (e.g. narrating numbers it didn't
choose), so a code-only gate suffices.  Pure functions earlier in the
pipeline (here: `insights/deterministic.py`) supply the numeric facts.

### Option B — Sanitizer-as-tool / Synthesizer-as-judge (Layer 4 baselines)

Multiple sanitizer `@mcp.tool` functions run in parallel against the
extractor's proposal.  A separate synthesizer subagent (single LLM-judge)
reads proposal + flags and returns SynthesizerDecision.  PreToolUse hook
denies writes per the strict-mode contract.

Use when: external-fact ingestion.  Hallucination risk is high; multiple
sanitizers (schema, policy, source-tier) catch different failure modes.

### Option C — Evaluator-optimizer (Layer 6 scenario compiler)

A scenario-compiler agent emits a proposal.  A scenario-evaluator agent
critiques it against a rubric.  On low score, the proposal loops back
through the compiler with feedback.  SDK Stop hook bounds the loop at N=2.

Use when: the artifact is exploratory and benefits from one round of
self-critique, but cost discipline forbids unbounded loops.  On loop
exhaustion, the synthesizer commits with `disputed=true`.

### Option D — Constitutional critique-revise (Layer 5 curation)

The agent operates under a written constitution (the system prompt).  Each
turn includes a critique step ("does this output violate any rule in the
constitution?") followed by a revision step.  Strict mode: never
auto-applies — the synthesizer always escalates to `ctx.elicit()`.

Use when: refresh of a source-of-truth artifact (e.g. `sources_registry.yaml`)
where humans must approve every diff.

## Mapping summary

| Layer | Artifact                       | Mode    | Option | Confidence floor | Block sev |
|-------|--------------------------------|---------|--------|------------------|-----------|
| 4     | `baselines/data/*.yaml`        | Strict  | B      | 0.85             | high      |
| 5     | `curation/sources_registry.yaml` | Strict | D      | 0.85             | high      |
| 6     | `scenarios` row                | Soft    | C      | 0.70             | critical  |
| 8     | `insights` row                 | Soft    | A      | 0.60             | critical  |

## Auditability — the durable artifacts

For every agent interaction we persist two rows in Postgres:

- `observations` — every sanitizer flag, winning AND losing.  No silent dedup.
- `syntheses`    — every synthesizer decision with confidence + rationale.

Replaying a decision is a join: `syntheses ⨝ observations on subject_id`.

This is the project-wide application of Anthropic's "minimize the game of
telephone" guidance — durable artifacts, not chat messages.

## Implementation pointers

- `orchestrator/deliberation.py` — DeliberationContract dataclass + the
  module-level `CONTRACTS` registry mapping layer → contract.
- `orchestrator/hooks.py` — `make_sanitize_pretooluse_hook(contract)` and
  `make_synthesize_posttooluse_hook(contract)`.  Both read the contract for
  the layer and apply the appropriate policy.  Both emit telemetry events
  for every gate decision.
- `orchestrator/agents.py` — central registry of every agentic role,
  including the shared `synthesizer` agent (Opus 4.7) used across all
  strict-mode commits.
- Per-layer servers (M12, M13, M15, M16) wire `hooks_for_layer(layer)` into
  the SDK's `hooks=` argument when constructing the orchestrator client.

## What's intentionally NOT here

- `bypassPermissions` and `acceptEdits` are FORBIDDEN on every
  AgentDefinition.  They are inherited and unrevocable.  Write gating is
  via the PreToolUse hook plus the `allowed_tools` allowlist — never via
  permission-mode escape hatches.
- Layer servers MUST NOT call each other as Clients.  Cross-layer access
  goes through the orchestrator (RFC 8693 token exchange in `infra/auth.py`).
