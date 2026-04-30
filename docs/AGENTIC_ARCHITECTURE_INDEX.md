# Agentic Architecture Index

Source: `docs/architecture_research.pdf` (April 2026 snapshot, 25 pages).
This index is the structural lookup for the agentic architecture. The PDF
is the authoritative explanation; this file is the fast-reference for
M12–M18 milestones.

## 1. Agentic vs deterministic layer map

| Layer | Owns                       | Agentic? | Mode    | Confidence floor | Severity threshold | Option                              | Rationale (PDF §)                                                                                                  |
|-------|----------------------------|----------|---------|------------------|--------------------|-------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| 0     | Infra                      | No       | n/a     | n/a              | n/a                | n/a                                 | Boilerplate; deterministic by definition. (§3 layer table)                                                          |
| 1     | Event substrate            | No       | n/a     | n/a              | n/a                | n/a                                 | Raw event log; agents have nothing to add. (§3 layer table)                                                         |
| 2     | Algorithms                 | No       | n/a     | n/a              | n/a                | n/a                                 | Pure functions; agent involvement breaks determinism. (§3 layer table; reinforced §7.5)                             |
| 3     | Parameters                 | No       | n/a     | n/a              | n/a                | n/a                                 | Inputs to algorithms; tuning is calibration's job, not an agent's. (§3.1, §7.5)                                     |
| 4     | Baselines (external facts) | Yes      | Strict  | 0.85             | high               | B — sanitize-as-tool + judge        | External facts (Meta DAP, run rate, competitor metrics) need full sanitize/synthesize ingestion. (§3.2, §9.2)       |
| 5     | Curation (sources registry) | Yes     | Strict  | 0.85             | high               | D — constitutional critique-revise  | Constitution: cite primary > secondary; never auto-apply diffs; ctx.elicit() always. (§3.3)                         |
| 6     | Engine + scenario compiler | Mixed    | Soft (sublayer only) | 0.70 | critical           | C — evaluator-optimizer (N=2)       | Engine deterministic (M7-locked); scenario synthesis sublayer is agentic with soft gate. (§3.4)                     |
| 7     | Metrics DSL                | No       | n/a     | n/a              | n/a                | n/a                                 | SQL templates; agent-written SQL is unsafe (injection, hallucinated joins, non-determinism). (§3.5, §7.9)           |
| 8     | Insights (narration)       | Yes      | Soft    | 0.60             | critical           | A — hook-only gate                  | Narrator must not invent numeric values; numbers come from `insights/deterministic.py`. PostToolUse-only. (§3.6)    |
| 9     | Calibration                | No       | n/a     | n/a              | n/a                | n/a                                 | Tests + lock; deterministic by design. The lock IS the gate. (§3 layer table; §7.7)                                  |
| 10    | Interview tools            | LLM-gated, not agentic | n/a | n/a   | n/a                | n/a                                 | Uses Sonnet 4.6 internally but no sanitize/synthesize / no observations table; gated by `@requires_calibration`. (§3.7) |

**Note on Layer 6's "Mixed" status.** The engine itself (`engine/scenario.py`,
`engine/perturbations.py`, `engine/tick.py`, `engine/simulator.py`,
`engine/state.py`) is deterministic and was locked under calibration in
M7/M11. Same `(scenario.perturbations, scenario.seed)` produces byte-
identical event sequences on the substrate ordering tuple. The agentic
piece is the **scenario-synthesis sublayer** introduced in M15: a Sonnet-
4.6 compiler translates natural-language hypotheticals (e.g., "what if
Meta launched Reels-only mode for teens") into structured perturbation
manifests via the evaluator-optimizer pattern (compiler proposes →
evaluator critiques against rubric → on score < 0.7, retry, bounded
N=2). Soft mode allows exploratory failure: on N=2 exhaustion the
synthesizer commits with `disputed=true` rather than rejecting outright.
The deterministic engine consumes whatever manifest the agentic
sublayer produces, so the calibration lock remains binding even on
agentic-authored scenarios. (PDF §3.4)

## 2. Sanitize/synthesize gate policy

Two gate compositions (strict and soft), four architectural options
(A/B/C/D) mapping to specific layers, and the closed registry of
`synthesize_*` tools that are the sole writers to canonical state.
Cite PDF §2 (the three-step pattern), §9.2 (strict gate stack), §9.3
(soft gate stack).

### 2.1 Strict gate composition (PDF §9.2)

The full stack used for L4 baselines (M12) and L5 curation (M13):

1. **Schema sanitizer** — deterministic; validates ExtractedMetric
   against the canonical Pydantic schema. ~$0.0001 / call.
2. **Provenance sanitizer** — deterministic; checks
   `{value, source, provenance, confidence}` block presence and
   non-emptiness. ~$0.0001 / call.
3. **Source-tier sanitizer** — deterministic; resolves source through
   `curation/sources_registry.yaml` and checks trust tier
   (1=SEC > 2=earnings_call > 3=analyst > 4=blog).
4. **Bound sanitizer** — deterministic; checks value within plausible
   range derived from existing baselines (e.g. percentages in [0, 100],
   eCPM not 100× expected).
5. **Synthesizer (Opus 4.7)** with `thinking_budget=16000`, ~$0.10 /
   call; reads the proposal plus accumulated sanitizer flags and
   returns a `SynthesizerDecision(decision, confidence, rationale,
   accepted_fixes, unresolved_disputes)`.
6. **PreToolUse hook blocks** if `confidence < 0.85` OR
   `severity ≥ high` (per the layer's DeliberationContract from M1).
7. **`ctx.elicit()` for human approval** on borderline cases or
   `decision == "escalate"`. The escalation path is the strict gate's
   answer to high-uncertainty proposals — never auto-write under
   uncertainty.

Cost per write: **~$0.10 + sanitizer overhead.** Latency: **~5–10s.**
Appropriate for high-stakes writes (calibrated baselines); over-
engineering for narration. (PDF §9.2)

### 2.2 Soft gate composition (PDF §9.3)

The lighter stack used for L6 scenario synthesis (M15) and L8 insights
(M16):

1. **PostToolUse hook annotates** the output with
   `{disputed, confidence, disputed_by}` so downstream consumers can
   see uncertainty.
2. **No PreToolUse block** — soft mode allows the write; the user
   reads the output and has agency to push back.
3. **Optional LLM-as-judge** for ambiguous content, but **not gating**.
   When invoked it informs the `disputed` flag rather than blocking
   the write.

Cost per write: **~$0.001** (annotation only). Latency: **<1s.** The
cost of an off-tone insight is low; the cost of always-strict is high
(latency, $$, false positives). (PDF §9.3)

### 2.3 Four options mapped to layers (PDF §2.2)

| Option | Pattern                                        | Layer                              | Implementation complexity | False positive rate              | Token cost                                          | When to use                                                            |
|--------|------------------------------------------------|------------------------------------|----------------------------|----------------------------------|------------------------------------------------------|-------------------------------------------------------------------------|
| A      | Hook-only gate                                  | L8 insights                         | Low                        | Low                              | Minimal (deterministic hooks)                        | Soft gate, low-stakes outputs (narration)                              |
| B      | Sanitizer-as-tool + synthesizer-as-judge        | L4 baselines                        | Medium                     | Medium                           | Sanitizer (Haiku 4.5) + synthesizer (Opus 4.7)       | Strict gate, high-stakes writes                                        |
| C      | Evaluator-optimizer loop (N=2)                  | L6 scenario synthesis sublayer       | Medium–high                | Low (iterates until quality)     | N × (compiler + evaluator)                           | Soft gate, exploratory outputs where iteration improves quality        |
| D      | Constitutional critique-revise                  | L5 curation                          | Medium                     | Medium                           | Single LLM call with critique–revise loop            | Strict gate, where the constitution is well-articulated                |

The four options share the three-step skeleton (agent proposes →
sanitize/critique → synthesize/commit) but differ in **where the
judgment lives**. Option A pushes judgment into the deterministic
hook; Option B pushes it into a separate Opus judge; Option C makes
the agent self-iterate against an evaluator; Option D wraps the agent
in a written constitution and forces a critique-revise turn within
the same model call. (PDF §2.2)

### 2.4 `synthesize_*` tool registry — the sole writers

Every write to canonical state goes through one of these five tools.
Every other tool in the agentic layers is `readOnlyHint=True`.

| Tool                       | Milestone | Writes to                              | Gate mode  | Option | Confidence floor |
|----------------------------|-----------|-----------------------------------------|------------|--------|-------------------|
| `synthesize_baseline`      | M12       | `baselines/data/*.yaml`                 | Strict     | B      | 0.85              |
| `synthesize_diff`          | M13       | `curation/sources_registry.yaml`        | Strict     | D      | 0.85              |
| `synthesize_scenario`      | M15       | `scenarios` table (Postgres)            | Soft       | C      | 0.70              |
| `synthesize_insight`       | M16       | `insights` table (Postgres)             | Soft       | A      | 0.60              |
| *(no L7 metrics synthesizer)* | —      | —                                       | —          | —      | —                 |

There is **no L7 metrics synthesizer.** Metrics are human-authored
SQL templates per PDF §3.5 / §7.9 (agent-written SQL is unsafe:
injection via dimension whitelist bypass, hallucinated joins, non-
determinism). Agents may *select* metrics through `MetricRuntime`;
they may not *generate* them.

**The sole-writer rule** (PDF §2.3): every tool other than these four
in the agentic layers is read-only. Sanitizers, extractors, evaluators,
narrators all return data without writing canonical state. Only
`synthesize_*` tools call the disk / DB write paths. The provenance
audit (M8) and the layer-import linter (M18) enforce this; the
PreToolUse hook from PDF §10.4 (replicated as a code skeleton in §7
of this index) blocks any non-synthesizer tool's attempt to write to a
deterministic-layer path.

## 3. FastMCP composition rule

**Single front door via `mount()`. Never chained Client calls when sampling is needed.** (PDF §1.1)

One outer FastMCP server (`infra/server.py`, the front door) with four
sub-servers mounted under layer-numbered namespace prefixes. All tools,
resources, and prompts surface through the front door's transport.
Process memory and event loop are shared, which is what preserves the
bidirectional sampling channel.

```python
# infra/server.py — pattern from PDF §10.1
import os
from fastmcp import FastMCP
from fastmcp.transports import StreamableHTTPSessionManager
from infra.auth import oauth_provider
from infra.eventstore import RedisEventStore

# Sub-servers — each is its own FastMCP() instance in its own module:
from baselines.server import baselines_server     # FastMCP("baselines")
from curation.server  import curation_server      # FastMCP("curation")
from engine.server    import engine_server        # FastMCP("engine_compiler")
from insights.server  import insights_server      # FastMCP("insights")

front_door = FastMCP("Reels Hybrid", auth=oauth_provider)

# Mount under layer-numbered prefixes
front_door.mount("l4", baselines_server)
front_door.mount("l5", curation_server)
front_door.mount("l6", engine_server)
front_door.mount("l8", insights_server)

# Stateful session manager with Redis EventStore (see §4)
session_manager = StreamableHTTPSessionManager(
    stateless=False,
    event_store=RedisEventStore(os.environ["REDIS_URL"]),
)

if __name__ == "__main__":
    front_door.run(
        transport="streamable-http",
        session_manager=session_manager,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
    )
```

### Why chained Client breaks sampling — the four-step trace (PDF §1.2)

The MCP sampling flow:

1. A tool inside the baselines server needs to call back to the user's
   model. It invokes `ctx.sample(messages=[...])` or `ctx.elicit(...)`.
2. FastMCP translates this into a `sampling/createMessage` JSON-RPC
   request.
3. The request travels from the server back through the active
   transport to the connected client.
4. The connected client (the Claude Agent SDK orchestrator) routes the
   request to the user's configured model and returns the response
   back along the same path.

**Pattern A (`mount()`) — preserves the channel.** The active transport
is the front-door's Streamable HTTP connection to the orchestrator. The
sampling request reaches the orchestrator's Agent SDK client, which
routes to Claude. Response flows back along the same path.

**Pattern B (chained `Client`) — silently breaks the channel.** The
inner server's "active transport" is the connection to the outer
server's `Client` wrapper, **not** to the orchestrator. The sampling
request reaches the outer server, which has no logic to forward it to
its own upstream transport. The request times out or returns an error.
**No exception by default — sampling just doesn't work, and tests that
don't exercise sampling pass.** This is the silent-failure mode the
PDF flags as the single most expensive composition mistake.

### When `mount()` is mandatory vs when chained `Client` is acceptable (PDF §1.3)

| Need                                | Pattern              | Rationale                                                |
|-------------------------------------|----------------------|----------------------------------------------------------|
| Tools / resources / prompts only    | Either               | No sampling, no risk                                     |
| **Sampling required**               | **`mount()` only**   | Preserves transport identity                             |
| **Elicitation required**            | **`mount()` only**   | Same channel as sampling                                 |
| **Roots negotiation**               | **`mount()` only**   | Same channel                                             |
| Process isolation needed            | Chained `Client` (but no sampling) | Trade-off                                  |
| Different-language sub-servers      | Chained `Client` (no sampling)     | Trade-off                                  |

For the Reels simulator, every agentic sub-server (baselines, curation,
engine compiler, insights) needs sampling. **All four use `mount()`.**
There is no production reason on a Replit Reserved VM single-Python-
process deployment to reach for chained Client (PDF §1.5).

### Tool namespacing (PDF §1.4)

When a sub-server is mounted at prefix `"l4"`, its tools surface at the
front door as `l4_<tool_name>`. Resources surface as `l4://...`.
Prompts get the same prefix.

| Sub-server    | Mount prefix | Example tool name at front door         |
|---------------|--------------|------------------------------------------|
| baselines     | `l4`         | `l4_extract_metric`, `l4_synthesize_baseline` |
| curation      | `l5`         | `l5_diff_proposal`, `l5_synthesize_diff` |
| engine compiler | `l6`       | `l6_compile_scenario`, `l6_synthesize_scenario` |
| insights      | `l8`         | `l8_narrate_anomalies`, `l8_synthesize_insight` |

The mount prefix matches the **layer number**, not the sub-server's
internal name. This makes the layer architecture visible at the wire
level — a tool name like `l4_extract_metric` immediately tells a
debugger which layer is doing the work. Name collisions across mounted
sub-servers are handled by the prefix; two sub-servers can both define
a tool called `synthesize` because they surface as `l4_synthesize` and
`l8_synthesize`. (PDF §1.4)

## 4. Replit deployment constraints

Five non-negotiable deployment rules. The PDF flags each as a footgun
where the wrong choice fails silently and surfaces only when the
agentic flow needs the broken capability.

### 4.1 Stateful Streamable HTTP — mandatory (PDF §5.1)

The MCP spec defines two HTTP transport modes:

- **Stateless** — each request is independent, no session id. Server
  cannot push messages back to the client → **no sampling, no
  elicitation, no roots.**
- **Stateful** — sessions tracked via `Mcp-Session-Id` header. Server
  can push messages back via the same connection → sampling,
  elicitation, roots all work.

**For agentic FastMCP servers, stateful is the only choice that
works.** Stateless is a footgun: the server boots fine, tools register
fine, but `ctx.sample` returns an error and tests pass anyway because
they don't exercise sampling. (PDF §5.1, repeated as anti-pattern §7.2)

```python
# infra/server.py — required configuration
session_manager = StreamableHTTPSessionManager(
    stateless=False,                                      # MUST be False
    event_store=RedisEventStore(os.environ["REDIS_URL"]), # see §4.2
)

front_door.run(
    transport="streamable-http",
    session_manager=session_manager,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)
```

### 4.2 Redis EventStore for resumability (PDF §5.2)

When a Replit replica restarts (deploy, scaling event, network hiccup),
in-process session state vanishes. Without an external EventStore, the
client's reconnect loses its session and any in-flight sampling
requests are lost.

The `EventStore` interface persists per-session events to Redis (or
Upstash) keyed by session id. When the client reconnects with a
`Last-Event-ID: <id>` header, the session manager pulls events since
that id from Redis and replays them. **Sampling requests in flight at
the time of restart resume cleanly.**

```python
# infra/eventstore.py — sketch from PDF §5.2
class RedisEventStore(EventStore):
    def __init__(self, redis_url: str):
        self.r = redis.from_url(redis_url)

    async def append(self, session_id: str, event: dict) -> str:
        event_id = str(uuid4())
        await self.r.xadd(
            f"mcp:session:{session_id}",
            {"event_id": event_id, "data": json.dumps(event)},
        )
        return event_id

    async def since(self, session_id: str, last_event_id: str) -> list[dict]:
        # Read events after last_event_id from the Redis stream.
        ...
```

**Production Redis is mandatory for multi-replica.** Local Redis on the
same VM is acceptable for single-developer development; for production
multi-replica, configure `REDIS_URL` to point at Upstash or another
managed Redis.

### 4.3 Postgres for canonical state, NOT SQLite (PDF §5.3)

Replit Deployments may scale or redeploy at any time. **Filesystem
writes are not durable across redeploys.** A SQLite file written to
`./events.db` on one replica disappears when the replica is replaced.

Use Postgres (Replit's managed Postgres or external) for every
canonical table:

| Table          | Owner               | Source of truth |
|----------------|---------------------|------------------|
| `scenarios`    | M7 engine            | Postgres         |
| `events`       | M2 substrate         | Postgres         |
| `observations` | sanitizer flags      | Postgres         |
| `syntheses`    | synthesizer decisions | Postgres        |
| `baselines` mirror | M4 baselines (canonical is yaml) | Postgres for fast lookup |
| `params` mirror    | M3 params (canonical is yaml)    | Postgres for fast lookup |
| `evaluations`  | interview answer evaluations | Postgres |

SQLite is acceptable in **two narrow cases**:

- **Tests.** `tests/` use `aiosqlite` for speed and isolation. The
  `infra/db.py` module uses dialect-variant types so the same code
  runs against both Postgres and SQLite.
- **Local development.** Single-developer, single-replica, willing to
  reset state on redeploy.

**Production never uses SQLite.**

### 4.4 Reserved VM, NOT Autoscale (PDF §5.4)

| Need                                  | Choice              | Rationale                              |
|---------------------------------------|---------------------|-----------------------------------------|
| Sampling latency-sensitive            | Reserved VM         | Cold-starts on Autoscale add 10–30s    |
| Long-running agentic sessions          | Reserved VM         | Autoscale may evict mid-session        |
| Cost-conscious development             | Autoscale            | Pay-per-request                        |
| Multi-replica redundancy               | Reserved VM × N      | Load balancer in front                 |

For the Reels simulator, **Reserved VM is the right choice.** Sampling
latency materially affects user experience, and agentic sessions can
run minutes (M12 baseline ingestion via Files API; M15 scenario
synthesis with N=2 evaluator-optimizer iterations). Autoscale's
cold-start would inject multi-second latency into every `ctx.sample`
on a freshly-warmed replica.

### 4.5 Environment variables (PDF §5.5)

`.replit` exports:

```ini
# Build-time flags
CLAUDE_CODE_FORK_SUBAGENT=1     # ~90% input-token savings on subagent fan-out (PDF §4.3)
CLAUDE_CODE_ENABLE_TELEMETRY=1  # OTel export wired in infra/telemetry.py
```

Replit Secrets (never committed to git; `.env.example` documents the
keys with empty values):

```ini
MCP_AUTH_TOKEN=<oauth provider token>
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
REDIS_URL=rediss://...
DATABASE_URL=postgresql+asyncpg://...
```

The `CLAUDE_CODE_FORK_SUBAGENT=1` flag is "essentially mandatory" per
PDF §4.3 for Replit Reserved VM deployments with multiple agentic
layers. Without forking, each subagent loads independently — on Opus
that's 60+ seconds of cold-start per subagent.

### 4.6 Prompt cache TTL — explicit `"ttl":"1h"` on every block (PDF §6.1)

Anthropic's prompt cache defaults to a 5-minute TTL. For chat-style
applications this is fine. **For agentic workflows that pause for
elicitation** (`ctx.elicit` calls a human, the human takes 30 seconds
to respond), **the cache expires mid-flow.** The default 5-minute TTL
silently dropped from 5 minutes to itself in March 2026 reaffirms how
load-bearing the explicit setting is.

Set `"ttl":"1h"` explicitly on every `cache_control` block:

```python
client.messages.create(
    model="claude-sonnet-4-6",
    system=[
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},  # explicit
        }
    ],
    messages=[...],
)
```

What to cache (PDF §6.2): system prompts, tool definitions, reference
documents (e.g., `reels_metrics_comprehensive_v2.md` for the M12
ingestion step), few-shot example libraries.

What NOT to cache (PDF §6.3): user-specific content, scenario-specific
state, recent tool results.

Cost model (PDF §6.5): cache reads = 10% of base input price; cache
writes = 1.25× base. Break-even at ~5 reads per write; >50 reads
gives >80% savings. For one-shot tools, caching is net-negative —
don't cache.

## 5. Anti-pattern catalog

Eleven do-nots from PDF §7. The "where temptation arises" column is
the most load-bearing — it tells future contributors which file or
layer to inspect first when reviewing an agentic change. The PDF
explicitly notes that several of these fail silently (sampling-channel
breaks, cache-TTL drift, calibration-lock skips), so reviewers cannot
rely on test failures to catch them.

| #    | Anti-pattern                                                    | Where temptation arises                                                | Why it fails                                                                                                                  | The right pattern                                                                                                                  |
|------|-----------------------------------------------------------------|------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| 7.1  | Chained MCP `Client` calls when sampling is needed              | `infra/server.py` initial composition                                   | Inner server's `ctx.sample` reaches outer server, which has no logic to forward to the orchestrator's transport. Silent fail.   | `mount()` composition. Single transport, sampling preserved. (See §3 of this index.)                                              |
| 7.2  | Stateless HTTP transport on Replit                              | `infra/server.py` transport configuration                               | Stateless mode disables sampling, elicitation, roots. Tests that don't exercise these capabilities pass anyway.                | `stateless=False` on `StreamableHTTPSessionManager`, `Mcp-Session-Id` header, Redis EventStore. (§4.1, §4.2 of this index.)        |
| 7.3  | Combine Citations API + Structured Outputs in one call          | `baselines/tools/extract_metric.py`                                     | Returns 400; the two features are mutually exclusive in a single API call.                                                     | Two-call sequence: call 1 = Citations to gather cited evidence; call 2 = strict tool use to format the extracted metric.            |
| 7.4  | Use Opus on every layer                                          | `orchestrator/agents.py` model assignment                               | Cost. Opus 4.7 ≈ 5× Sonnet 4.6 at same token volume. Workers don't need Opus.                                                  | Sonnet 4.6 for extraction / narration / evaluation. Haiku 4.5 for cheap sanitizers. Opus 4.7 only for orchestrator + final synthesis. |
| 7.5  | Agents on Layers 1, 2, 3, 6 (engine), 7, 9                       | "Wouldn't it be nice if an agent could tune ranking weights?"           | Corrupts the calibrated core. Algorithm semantics depend on stable params; metrics depend on stable SQL; calibration depends on deterministic engine. | Agents on Layers 4, 5, 6 (scenario synthesis sublayer only), 8. Everything else stays deterministic.                              |
| 7.6  | Let agents call `open()` on yaml files                          | Any tool that "needs" to write a value to yaml as part of its job       | Bypasses the sanitize gate. The synthesizer is the sole writer.                                                                | All writes go through `synthesize_*` tools. Sanitizers are read-only (`readOnlyHint=True`). Provenance audit catches violations.   |
| 7.7  | Skip the calibration lock verify on agentic milestones          | "M12 just creates new files in `baselines/server.py`, why need the check?" | If `params/` or `baselines/data/` drifted since lock, the agent's outputs are based on uncalibrated state. Drift may not surface until M14+. | Every agentic milestone runs `verify_lock_against_current_state()` as the first step. Abort on drift.                              |
| 7.8  | Multi-agent for tightly-coupled tasks                           | "I'll spawn three subagents in parallel to handle these three sanitizers." | Three sanitizers are functions of the same input and produce flags consumed by the same downstream synthesizer. Coordination overhead, no parallelism gain. | Multi-agent for genuinely independent subtasks. For coupled tasks, sequential or parallel-tool-call within one agent.            |
| 7.9  | Trust agent-generated SQL                                       | Layer 7 metrics, Layer 8 insights ("can the narrator just write a custom query?") | SQL injection via dimension whitelist bypass; hallucinated joins; non-determinism between runs.                                | Metrics are human-authored SQL templates. The compiler enforces a closed `group_by` whitelist. Agents may select metrics; they may not generate them. |
| 7.10 | Rely on the 5-minute cache default                              | Forgetting to set `"ttl":"1h"` because the default mostly works         | Agentic workflows that pause for `ctx.elicit` exceed 5 minutes. Cache expires mid-flow; subsequent calls reload the entire system prompt. | `"ttl":"1h"` explicit on every `cache_control` block. (§4.6 of this index.)                                                       |
| 7.11 | Nest tool calls more than 3 deep                                 | Composed agentic flows where Tool A → subagent → Tool B → Tool C        | Context-window pressure (each level adds tool defs + history); debugging difficulty (which level produced the error?); latency multiplication. | Flatten. If the flow is 4+ deep, the architecture has wrong decomposition. Sanitizers are parallel siblings of the agent, not nested under it. |

## 6. Sampling channel preservation rules

The sampling channel is the mechanism by which an MCP server requests
a completion from the user's model. **Preserving this channel is the
single most important architectural concern in the agentic layers.**
(PDF §8 opening paragraph.)

The flow recap (PDF §8.1; cross-reference §3 of this index for the
four-step trace and the Pattern A vs Pattern B comparison):
tool → `ctx.sample(...)` → `sampling/createMessage` JSON-RPC →
active transport → connected client (Agent SDK orchestrator) →
user's configured model → response back along the same path.

### What breaks the channel (PDF §8.2)

| #   | Failure mode                                              | Specific file / config that introduces the break                                                                          |
|-----|-----------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| 1   | Stateless transport                                       | `infra/server.py` — `StreamableHTTPSessionManager(stateless=True)`. Server has no way to push messages back; sampling has nowhere to go. |
| 2   | Chained `Client` calls                                    | Pattern B from §3 of this index. Intermediate server has no transport pointing to the orchestrator.                       |
| 3   | Process isolation without channel forwarding              | A subprocess server with its own transport pointing at its parent process, not the orchestrator. FastMCP doesn't forward sampling across the boundary. |
| 4   | Dead transport connections                                | Replit's default proxy idle timeout is 60s. Without keepalives, long-poll connections get reaped silently.                |
| 5   | Missing capability declarations                           | Agent SDK orchestrator config that omits `capabilities: {"sampling": {}}` during the MCP handshake. Sampling rejected at the protocol level. |

### What preserves the channel (PDF §8.3)

| #   | Preservation rule                                         | Implementation pointer                                                                                                     |
|-----|-----------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| 1   | Stateful Streamable HTTP                                  | `infra/server.py` — `StreamableHTTPSessionManager(stateless=False)` (§4.1).                                                |
| 2   | `mount()` composition                                     | `front_door.mount("l4", baselines_server)` etc. (§3 of this index).                                                        |
| 3   | Explicit capability declarations                          | Agent SDK orchestrator config: `capabilities: {"sampling": {}}` declared during handshake.                                 |
| 4   | Transport keepalives                                      | WebSocket / SSE pings every 30 seconds; default 60-second proxy idle timeout will not reap the connection.                |
| 5   | Proper session management                                 | `Mcp-Session-Id` present on every request; `RedisEventStore` configured for resumability across replica restarts (§4.2).  |

### M18 acceptance canary (PDF §8.4)

The M18 acceptance test suite includes:

> *"Sampling channel: a layer4 tool successfully calls `ctx.sample()`
> back to the orchestrator."*

**This is the canary.** If it passes, the channel is intact. If it
fails, fix before shipping. The PDF explicitly notes that several
common silent failures look like:

- Tool returns `None` from `ctx.sample` instead of a completion →
  sampling reaches the client but the client isn't routing to a
  model. **Check Agent SDK config.**
- Tool times out on `ctx.sample` → request never reached the client.
  **Check transport mode and composition pattern.**
- Tool returns a completion but the content is empty → the model is
  being called but with empty messages. **Check serialization.**

The canary covers all three by exercising a real `ctx.sample()` round
trip from a Layer 4 tool through the front door to the orchestrator
and back. M18's `tests/integration/test_acceptance.py` will register
this as a single PASS/FAIL check; failure blocks shipping.

### Cost attribution (PDF §8.5)

**Sampling requests bill against the user's account, not the
server's.** The user's API key is the one used for the actual model
call. The MCP server is just routing.

Implications:

- **Usage tracking** must be done at the orchestrator level, not the
  server level. Per-tool token counts surfaced from server-side
  telemetry would underreport real cost.
- **Rate limits** apply to the user's tier, not the server's. A
  rate-limited user breaks every agentic flow that calls
  `ctx.sample()` until the limit resets.
- **API-key revocation** by the user does not affect the MCP server
  itself; the server keeps running but sampling fails. Health checks
  on the server alone won't surface this — the M18 canary will.

## 7. Code skeleton index

Five reference skeletons from PDF §10. Each one is the canonical shape
for the named pattern; the implementing milestone copies the structure
into the named target file and adapts to its layer's specifics.

| Pattern                                  | PDF section | Implementing milestone | Target file                                   |
|------------------------------------------|-------------|-------------------------|-----------------------------------------------|
| `mount()` composition                    | §10.1       | M12                     | `infra/server.py`                             |
| Sanitize tool (deterministic, `readOnlyHint=True`) | §10.2 | M12             | `baselines/tools/sanitize_*.py` (then M13/M15/M16 follow the shape) |
| Synthesizer pattern (sole writer)        | §10.3       | M12                     | `baselines/tools/synthesize_baseline.py` (then M13: `curation/tools/synthesize_diff.py`; M15: `engine/tools/synthesize_scenario.py`; M16: `insights/tools/synthesize_insight.py`) |
| PreToolUse layer-isolation hook          | §10.4       | M12                     | `orchestrator/hooks.py` (extends the M1 hook factory with the deterministic-paths denylist) |
| Calibration verify wrapper (`@requires_calibration_unlocked`) | §10.5 | M12 | `calibration/lock.py` decorator applied to every `synthesize_*` tool across M12/M13/M15/M16 |

The §10.1 mount-composition skeleton is reproduced verbatim in §3 of
this index. The §10.3 synthesizer pattern is the load-bearing one —
its `verify_lock_against_current_state()` pre-call + `ctx.elicit()`
calibration-impact escalation + `write_yaml_atomic()` rename-after-tmp
sequence is what M12's first synthesize tool must implement, and the
shape repeats unchanged in M13/M15/M16.

## 8. Binding rules for M12–M18

Eight non-negotiable conventions. Each cites the PDF section that
grounds it; the M18 acceptance suite enforces every rule that is
test-detectable.

1. **Agentic code lives in existing layer directories** with new
   `server.py` + `tools/` + `prompts/` + `schemas.py` subdirs:

   | Layer | Directory      | Milestone | Notes                                            |
   |-------|----------------|-----------|--------------------------------------------------|
   | 4     | `baselines/`   | M12       | Strict gate (Option B), `synthesize_baseline`    |
   | 5     | `curation/`    | M13       | Strict gate (Option D), `synthesize_diff`        |
   | 6     | `engine/`      | M15       | Soft gate (Option C, scenario sublayer only); deterministic engine from M7 untouched |
   | 8     | `insights/`    | M16       | Soft gate (Option A, hook-only), `synthesize_insight` |
   | front-door | `infra/server.py` | M0 + M12 | M0 created the front door; M12 mounts the first sub-server (`l4`) |

2. **Every tool that writes canonical state MUST be a `synthesize_*`
   tool.** Sanitizers are `readOnlyHint=True`. The provenance audit
   (M8) and the layer-import linter (M18) enforce the single-writer
   rule. PDF §2.3 (the synthesizer-as-sole-writer rule, including the
   four documented failure modes when violated).

3. **Every agentic milestone starts with the calibration lock
   prelude check.** `verify_lock_against_current_state()` runs before
   anything else. Abort on drift before proceeding. PDF §10.5
   (`@requires_calibration_unlocked` decorator skeleton); reinforced
   as anti-pattern §7.7.

4. **Every `synthesize_*` tool ends with a calibration-impact
   post-check.** If the write would change a locked yaml hash, surface
   via `ctx.elicit()` for human approval before committing. The Layer
   4 strict gate also requires confidence ≥ 0.85 and severity < high.
   PDF §10.3 (synthesizer skeleton); §2.4 (calibration-impact pre-
   check pseudocode).

5. **Replit constraints are non-negotiable.** Stateful Streamable
   HTTP, Redis EventStore, Postgres (not SQLite) for canonical state,
   `"ttl":"1h"` explicit on every `cache_control`,
   `CLAUDE_CODE_FORK_SUBAGENT=1` in `.replit`. PDF §5 (the full
   constraints chapter); §6.1 (cache TTL); reinforced as anti-patterns
   §7.2 / §7.10.

6. **Citations API + Structured Outputs cannot combine in one call.**
   Two-call pattern: call 1 = Citations to gather cited evidence;
   call 2 = strict tool use (`strict:true`) to format the structured
   output. Applies to M12 baseline extraction
   (`baselines/tools/extract_metric.py`) and M13 curation refresh
   (`curation/tools/diff_proposal.py`). PDF §7.3.

7. **Multi-agent only for genuinely independent work.** Sanitizers
   are parallel `@mcp.tool` calls within one agent (siblings, not
   nested) — not parallel subagents. Tightly-coupled tasks that share
   inputs and feed the same downstream synthesizer don't benefit from
   process-level parallelism. PDF §7.8 / §7.11 (max 3 deep).

8. **The PDF wins on conflicts with prior context.** When this index
   or any milestone prompt disagrees with the PDF, the PDF is
   authoritative. Deterministic-core lessons from M5 (yaml ingestion
   conventions), M8 (provenance audit), and M11 (calibration lock)
   still apply — agentic layers are additive, not replacements. The
   architectural decisions in PDF §11 ("What's likely to change")
   that are flagged stable — `mount()`, sanitize/synthesize, agents
   only on Layers 4/5/6/8, calibration-as-hard-gate — are unlikely
   to change and are the safest things to anchor on.
