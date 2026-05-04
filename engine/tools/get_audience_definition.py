"""get_audience_definition — deterministic audience-cohort lookup.

Per architecture_research.pdf §3.4: the agentic compiler is allowed
to call deterministic helper tools with adaptive thinking interleaved.
This is one of those helpers — the compiler calls it to ground
audience-filter values instead of inventing dim/op/value triples.

Without this lookup, the compiler invents *teens* as
`{dim: "age", op: "lt", value: 18}`, which the engine doesn't know
how to filter on.  With it, the compiler gets back the canonical
`{dim: "viewer_segment", op: "eq", value: "teens_13_17"}` triple.

Annotated `readOnlyHint=True` — pure lookup, no writes.
"""
from __future__ import annotations

from typing import Any

from engine.server import engine_server


# Closed registry of named audience cohorts.  Adding an audience here
# is a deliberate review step: the substrate writer (M3) must support
# the dim/op/value combination, and the segment_propensities params
# must be wired for new viewer_segment values.  Do NOT add audiences
# here without a substrate-writer ticket.
_AUDIENCE_REGISTRY: dict[str, dict[str, Any]] = {
    "teens": {
        "dim": "viewer_segment",
        "op": "eq",
        "value": "teens_13_17",
    },
    "us_only": {
        "dim": "viewer_geo",
        "op": "eq",
        "value": "US",
    },
    "new_users": {
        "dim": "viewer_tenure_days",
        "op": "lt",
        "value": 30,
    },
    "core_users": {
        "dim": "viewer_tenure_days",
        "op": "gt",
        "value": 365,
    },
    "snackers": {
        "dim": "viewer_segment",
        "op": "eq",
        "value": "snackers",
    },
    "lurkers": {
        "dim": "viewer_segment",
        "op": "eq",
        "value": "lurkers",
    },
    "creators_micro_plus": {
        "dim": "creator_tier",
        "op": "in",
        "value": ["micro", "mid", "mega"],
    },
}


@engine_server.tool(annotations={"readOnlyHint": True})
async def get_audience_definition(audience_name: str) -> dict[str, Any]:
    """Look up a named audience cohort.

    Args:
        audience_name:  one of the keys in the audience registry.

    Returns:
        `{"dim": ..., "op": ..., "value": ...}` — directly usable as
        an `AudienceFilter` constructor argument.

    Raises:
        ValueError: if `audience_name` is not in the registry.  The
                    exception message lists every available name so the
                    compiler can self-correct on the next iteration.
    """
    key = audience_name.strip().lower()
    if key not in _AUDIENCE_REGISTRY:
        available = ", ".join(sorted(_AUDIENCE_REGISTRY.keys()))
        raise ValueError(
            f"unknown audience {audience_name!r}; available: [{available}]"
        )
    # Return a fresh dict so callers can't mutate the registry.
    return dict(_AUDIENCE_REGISTRY[key])
