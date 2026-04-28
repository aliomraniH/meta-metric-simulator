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

    app = FastMCP(
        name="Reels Hybrid",
        auth=auth,
        # Stateful streamable-http: required for sampling/elicitation/roots.
        stateless_http=False,
    )

    # Wire the Redis-backed EventStore for Last-Event-ID resumability.
    # FastMCP attaches this to the streamable-http transport on serve.
    app.event_store = RedisEventStore.from_env()  # type: ignore[attr-defined]

    # Mount agentic layer servers under stable namespaces.  Real
    # implementations replace these in later milestones; the namespace
    # contract (l4, l5, l6, l8) is fixed now so downstream tools can be
    # written against it.
    app.mount(_placeholder_server("baselines", "l4"), prefix="l4")
    app.mount(_placeholder_server("curation", "l5"), prefix="l5")
    app.mount(_placeholder_server("engine_compiler", "l6"), prefix="l6")
    app.mount(_placeholder_server("insights", "l8"), prefix="l8")

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
