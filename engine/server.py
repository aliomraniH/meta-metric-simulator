"""Layer 6 scenario synthesis sublayer (agentic, soft mode, Option C
from AGENTIC_ARCHITECTURE_INDEX.md §2.3).

Layer 6 is "Mixed" per the layer-map index: the deterministic engine
(M7, locked) computes simulation results; the agentic synthesis
sublayer translates natural-language hypotheticals into manifests
the engine can run.  The engine itself is unchanged by this server.

Tools registered here surface as `l6_*` at the front door:
  - l6_compile_scenario           — agentic CompiledScenario producer
  - l6_get_audience_definition    — deterministic audience-cohort lookup
  - l6_lookup_seasonality         — deterministic seasonality multipliers

M15a deliberately does NOT mount this into infra/server.py — that
happens in M15c after the synthesizer + evaluator exist, so the
front door never exposes a half-built strict-gate sub-server.

Layer-import discipline (per AGENTIC_ARCHITECTURE_INDEX.md §8.1):
  engine/ MAY import from algorithms/, params/, substrate/, fastmcp,
  pydantic, anthropic, stdlib.
  engine/ MAY NOT import from baselines/, calibration/, interview/,
  metrics/ (the engine writes events; metrics read them — separate
  layers), insights/ (insights consume metrics; engine doesn't see
  them), tools/.
"""
from __future__ import annotations

from fastmcp import FastMCP


engine_server = FastMCP("engine_compiler")


# Tool registration via decorator side-effect.  Imported AFTER the
# server instance is constructed so the decorator finds a live target.
from engine.tools import get_audience_definition  # noqa: E402, F401  (M15a)
from engine.tools import lookup_seasonality       # noqa: E402, F401  (M15a)
from engine.tools import compile_scenario         # noqa: E402, F401  (M15a)
from engine.tools import evaluate_scenario        # noqa: E402, F401  (M15b)
from engine.tools import synthesize_scenario      # noqa: E402, F401  (M15c — sole writer)
