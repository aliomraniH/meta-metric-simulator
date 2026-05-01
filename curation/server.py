"""Layer 5 — Curation (agentic, strict mode, Option D from AGENTIC_ARCHITECTURE_INDEX.md §2.3).

The curation sub-server hosts the agentic refresh of
`curation/sources_registry.yaml`: web_search + web_fetch helpers,
the diff_proposal two-call orchestrator, and the deterministic
sanitize_constitution check.  The synthesizer (`synthesize_diff`,
M13b) is the sole writer to the registry yaml — diff_proposal only
proposes.

Mounted at namespace `l5` in `infra/server.py` during M13b so a tool
registered here as `web_search` surfaces at the front door as
`l5_web_search`.

M13a deliberately does NOT mount this into infra/server.py — that
happens in M13b after `synthesize_diff` exists, so the front door
never exposes a half-built strict-gate sub-server.

Layer-import discipline (per AGENTIC_ARCHITECTURE_INDEX.md §8.1):
  curation/ MAY import from fastmcp, pydantic, stdlib, anthropic,
  baselines/tools/sanitize_source_tier (HIGH_STAKES_METRIC_IDS only),
  and calibration/lock (M13b).
  curation/ MAY NOT import from algorithms/, engine/, metrics/,
  params/, interview/, tools/.
"""
from __future__ import annotations

from fastmcp import FastMCP


curation_server = FastMCP("curation")


# Tool registration via decorator side-effect.  Imported AFTER the
# server instance is constructed so the decorator finds a live target.
from curation.tools import web_search             # noqa: E402, F401  (M13a)
from curation.tools import web_fetch              # noqa: E402, F401  (M13a)
from curation.tools import diff_proposal          # noqa: E402, F401  (M13a)
from curation.tools import sanitize_constitution  # noqa: E402, F401  (M13a)
from curation.tools import synthesize_diff        # noqa: E402, F401  (M13b — sole writer)
