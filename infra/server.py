"""Front-door FastMCP server for the Reels AT Simulator.

Composition rule (from the architecture spec): mount() — never chained Client
calls.  Mounting preserves the bidirectional sampling channel that Layer 4/5/8
specialists need to call ctx.sample() back to the orchestrator's Claude.

Stateful mode is REQUIRED.  Stateless streamable-http disables sampling,
elicitation and roots — all of which the agentic layers depend on.

Layer servers (l4 baselines, l5 curation, l6 engine, l8 insights) are mounted
here as placeholders.  The actual servers are built in M12, M13, M15, M16.
Until then mount points raise NotImplementedError on tool calls so callers fail
loudly rather than silently routing to nothing.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastmcp import FastMCP

from infra.auth import build_oauth_provider
from infra.eventstore import RedisEventStore
from infra.telemetry import init_telemetry

log = logging.getLogger(__name__)


def _placeholder_server(name: str, layer_id: str) -> FastMCP:
    """Stub layer server — replaced by real implementations in M12/M13/M15/M16.

    A bare FastMCP is mounted so the namespace exists and tools registered
    later land in the right place.  Calling any tool on this stub raises.
    """
    stub = FastMCP(name=name)

    @stub.tool(name=f"{layer_id}_placeholder")
    async def _placeholder() -> dict[str, str]:
        raise NotImplementedError(
            f"Layer {layer_id} server not yet implemented "
            f"(scheduled for milestone M12/M13/M15/M16)"
        )

    return stub


def build_app() -> FastMCP:
    """Construct the front-door FastMCP server with layer mounts."""
    init_telemetry()

    auth = build_oauth_provider()

    # Stateful streamable-http (required for sampling/elicitation/roots) is
    # passed at run-time via run_http_async(stateless_http=False) per
    # FastMCP 3.x API; the constructor no longer accepts stateless_http.
    app = FastMCP(
        name="Reels Hybrid",
        auth=auth,
    )

    # Wire the Redis-backed EventStore for Last-Event-ID resumability.
    # FastMCP attaches this to the streamable-http transport on serve.
    app.event_store = RedisEventStore.from_env()  # type: ignore[attr-defined]

    # Mount agentic layer servers under stable namespaces.  Real
    # implementations replace these in later milestones; the namespace
    # contract (l4, l5, l6, l8) is fixed now so downstream tools can be
    # written against it.
    #
    # Layer 4 (baselines) is real as of M12c-iii — agentic ingestion of
    # external facts via extract_metric + sanitize_* + synthesize_baseline.
    # Surfaces as l4_extract_metric, l4_sanitize_schema, l4_sanitize_policy,
    # l4_sanitize_source_tier, l4_synthesize_baseline at the front door.
    from baselines.server import baselines_server  # noqa: E402
    app.mount(baselines_server, namespace="l4")
    # Layer 5 (curation) is real as of M13b — agentic refresh of
    # sources_registry.yaml via web_search + web_fetch + diff_proposal +
    # sanitize_constitution + synthesize_diff (sole writer).  Constitution-
    # gated; never auto-applies (every accept goes through ctx.elicit).
    from curation.server import curation_server  # noqa: E402
    app.mount(curation_server, namespace="l5")
    # Layer 6 scenario synthesis sublayer is real as of M15c — agentic
    # compile_scenario + evaluate_scenario + synthesize_scenario (sole
    # writer to the scenarios table).  Soft mode (Option C from §2.3):
    # writes with disputed=true on N=2 evaluator-optimizer exhaustion.
    # The deterministic engine itself (M7) is unchanged.
    from engine.server import engine_server  # noqa: E402
    app.mount(engine_server, namespace="l6")
    app.mount(_placeholder_server("insights", "l8"), namespace="l8")

    return app


# Module-level instance — orchestrator and tests import this directly.
app = build_app()


def main() -> None:
    """Run the server over streamable-http on the Replit Reserved VM port."""
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8080"))
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    log.info("Starting Reels Hybrid MCP on %s:%s (stateful streamable-http)", host, port)
    app.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
