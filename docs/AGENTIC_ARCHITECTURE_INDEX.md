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
