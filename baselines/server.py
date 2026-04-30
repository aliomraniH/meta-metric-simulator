"""Layer 4 — Baselines (agentic, strict mode, Option B from AGENTIC_ARCHITECTURE_INDEX.md §2.3).

The baselines sub-server hosts the agentic ingestion of external facts:
extractor + sanitizers + reconciler/synthesizer.  Per the
`docs/AGENTIC_ARCHITECTURE_INDEX.md` §3 composition rule it is mounted
at namespace `l4` in `infra/server.py` so a tool registered here as
`sanitize_schema` surfaces at the front door as `l4_sanitize_schema`.

This file is the M12a scaffold:
  - The FastMCP("baselines") instance.
  - Imports the sanitize_schema tool so its @baselines_server.tool
    decorator runs and registers the tool.

M12a deliberately does NOT mount this into infra/server.py — that
happens in M12c after the synthesize_baseline tool exists, so the
front door never exposes a half-built strict-gate sub-server.

Layer-import discipline (per AGENTIC_ARCHITECTURE_INDEX.md §8.1):
  baselines/ MAY import from fastmcp, pydantic, stdlib, anthropic
  (M12c), and calibration/lock (M12c).
  baselines/ MAY NOT import from algorithms/, engine/, metrics/,
  params/, interview/, tools/.
"""
from __future__ import annotations

from fastmcp import FastMCP


baselines_server = FastMCP("baselines")


# Tool registration via decorator side-effect.  Imported AFTER the
# server instance is constructed so the decorator finds a live target.
# Each tool module imports `baselines_server` from this module — the
# import order here resolves the circular reference cleanly.
from baselines.tools import sanitize_schema       # noqa: E402, F401  (registers via decorator)
from baselines.tools import sanitize_policy       # noqa: E402, F401  (M12b)
from baselines.tools import sanitize_source_tier  # noqa: E402, F401  (M12b)
