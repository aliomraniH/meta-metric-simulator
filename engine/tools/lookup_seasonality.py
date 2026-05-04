"""lookup_seasonality — deterministic seasonality-multiplier lookup.

Per architecture_research.pdf §3.4: the compiler may call this to
ground perturbation magnitudes in known seasonality patterns.

The multipliers are advisory — the engine does NOT auto-apply them.
The compiler reads them, decides whether to encode a perturbation
that captures the seasonal signal (e.g., a `multiply` op on
`ad_load_pct` for Q4 ad-demand uplift), and the engine then runs
that perturbation deterministically.

Annotated `readOnlyHint=True`.
"""
from __future__ import annotations

from typing import Any

from engine.server import engine_server


# Closed registry of known seasonality patterns.  Multipliers are
# rough industry estimates; high-stakes calibration anchors should
# carry their own perturbation values from baselines/data/, not
# from this advisory lookup.  Adding a period is a deliberate review
# step.
_SEASONALITY_REGISTRY: dict[str, dict[str, float]] = {
    "Q4 2025": {
        "ad_demand_multiplier": 1.25,
        "viewing_time_multiplier": 1.10,
        "content_supply_multiplier": 1.05,
    },
    "Q1 2026": {
        "ad_demand_multiplier": 0.85,
        "viewing_time_multiplier": 0.95,
        "content_supply_multiplier": 0.98,
    },
    "summer_vacation_us": {
        "ad_demand_multiplier": 1.0,
        "viewing_time_multiplier": 1.15,
        "content_supply_multiplier": 0.92,
    },
    "back_to_school_us": {
        "ad_demand_multiplier": 1.0,
        "viewing_time_multiplier": 0.90,
        "content_supply_multiplier": 1.08,
    },
}

_NEUTRAL: dict[str, float] = {
    "ad_demand_multiplier": 1.0,
    "viewing_time_multiplier": 1.0,
    "content_supply_multiplier": 1.0,
}


@engine_server.tool(annotations={"readOnlyHint": True})
async def lookup_seasonality(period: str, region: str = "US") -> dict[str, Any]:
    """Look up advisory seasonality multipliers for a period.

    Args:
        period:  named period (e.g. "Q4 2025", "summer_vacation_us").
        region:  region code, default "US".  Reserved for future
                 region-specific multipliers; today the registry is
                 US-only and the kwarg is ignored.

    Returns:
        Dict with three multiplier keys + a `note` flag indicating
        whether the period was matched.  Unknown periods return all
        1.0 multipliers with `note='period_unknown'` so the compiler
        can choose to fall back to its own judgement.
    """
    table = _SEASONALITY_REGISTRY.get(period)
    if table is None:
        return {**_NEUTRAL, "period": period, "region": region, "note": "period_unknown"}
    return {**table, "period": period, "region": region, "note": "matched"}
