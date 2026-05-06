"""Layer 8 — Insights narrator (agentic, soft mode, Option A
hook-only from AGENTIC_ARCHITECTURE_INDEX.md §2.3).

The deterministic detectors live in `insights/deterministic.py`
(M14, agent-free).  This sub-server adds the narrator + the
two-stage retrieval helper + the sole-writer synthesizer.

Mounted at namespace `l8` in `infra/server.py` during M16.

Tools registered here surface as `l8_*` at the front door:
  - l8_find_similar_scenarios   — Voyage embed → rerank-2.5 retrieval
  - l8_narrate_anomalies        — agentic narrator (Sonnet 4.6)
  - l8_synthesize_insight       — sole writer to insights table

Layer-import discipline (per AGENTIC_ARCHITECTURE_INDEX.md §8.1):
  insights/ MAY import from metrics/, substrate/, models/, fastmcp,
  pydantic, voyageai, scipy, numpy, calibration/lock, infra/db,
  stdlib.
  insights/ MAY NOT import from algorithms/, engine/, params/,
  baselines/, interview/, tools/.
"""
from __future__ import annotations

from fastmcp import FastMCP


insights_server = FastMCP("insights")


# Tool registration via decorator side-effect.
from insights.tools import find_similar_scenarios  # noqa: E402, F401
from insights.tools import narrate_anomalies       # noqa: E402, F401
from insights.tools import synthesize_insight      # noqa: E402, F401  (sole writer)
