"""EventReader — parameterised reads over the events log.

Two methods:

  query(scenario_id, where=..., group_by=..., agg=...) — structured access
      used by the metric runtime when a metric definition can be expressed
      as a simple aggregation.

  raw_sql(scenario_id, sql, params=...) — SQL-template path used by the
      composable metric DSL (Layer 7).  The metric compiler validates the
      template; the reader just runs it.

Both paths require a scenario_id, both bind it as a parameter, and both
go through SQLAlchemy's text() so injection vectors are blocked.

The reader does NOT know about metric semantics, segments, or ranking.
Higher layers translate intent into queries; the reader executes them.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from infra.db import events as events_table

log = logging.getLogger(__name__)


# Columns we allow as group_by / where keys — anything else is rejected so
# the reader can't be coerced into a SQL injection vector via column names.
_ALLOWED_COLUMNS: frozenset[str] = frozenset({
    "tick_day", "event_type", "viewer_id", "viewer_segment", "viewer_geo",
    "viewer_tenure_days", "creator_id", "creator_tier", "reel_id",
    "reel_topic_cluster", "surface", "pool",
})

# Aggregations we expose; SQL functions only — no expressions.
_AGGREGATIONS: dict[str, str] = {
    "count":  "COUNT(*)",
    "sum_watch":   "SUM(watch_duration_sec)",
    "avg_watch":   "AVG(watch_duration_sec)",
    "sum_revenue": "SUM(ad_revenue_usd)",
    "sum_ads":     "SUM(ad_impression)",
    "distinct_viewers": "COUNT(DISTINCT viewer_id)",
    "distinct_reels":   "COUNT(DISTINCT reel_id)",
}


class EventReader:
    """Read-side companion to EventWriter."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ---- structured query ------------------------------------------------
    async def query(
        self,
        scenario_id: str,
        *,
        where: Mapping[str, Any] | None = None,
        group_by: Sequence[str] | None = None,
        agg: str = "count",
    ) -> list[dict[str, Any]]:
        if agg not in _AGGREGATIONS:
            raise ValueError(f"unknown agg {agg!r}; allowed: {sorted(_AGGREGATIONS)}")
        if group_by:
            for col in group_by:
                if col not in _ALLOWED_COLUMNS:
                    raise ValueError(f"group_by column {col!r} not allowed")
        if where:
            for col in where:
                if col not in _ALLOWED_COLUMNS and col != "event_type":
                    raise ValueError(f"where column {col!r} not allowed")

        select_cols = list(group_by or []) + [f"{_AGGREGATIONS[agg]} AS value"]
        where_sql = ["scenario_id = :scenario_id"]
        params: dict[str, Any] = {"scenario_id": scenario_id}
        for col, val in (where or {}).items():
            key = f"w_{col}"
            where_sql.append(f"{col} = :{key}")
            params[key] = val

        group_sql = f" GROUP BY {', '.join(group_by)}" if group_by else ""
        sql = (
            f"SELECT {', '.join(select_cols)} FROM events "
            f"WHERE {' AND '.join(where_sql)}{group_sql}"
        )
        result = await self._s.execute(text(sql), params)
        return [dict(r._mapping) for r in result]

    # ---- raw SQL (Layer 7 metric templates) ------------------------------
    async def raw_sql(
        self,
        scenario_id: str,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a parameterised SQL template.

        The template MUST reference :scenario_id (the metric compiler enforces
        this at registration time).  Additional params can be passed.
        """
        if ":scenario_id" not in sql:
            raise ValueError("raw_sql template must reference :scenario_id")
        merged: dict[str, Any] = {"scenario_id": scenario_id, **(params or {})}
        result = await self._s.execute(text(sql), merged)
        return [dict(r._mapping) for r in result]

    # ---- helpers used by tests ------------------------------------------
    async def count(self, scenario_id: str) -> int:
        result = await self._s.execute(
            select(events_table.c.event_id).where(events_table.c.scenario_id == scenario_id)
        )
        return len(result.fetchall())

    async def fetch_ordered(self, scenario_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        """Return events ordered by (tick_day, intra_day_seq) — the determinism fingerprint."""
        sql = (
            "SELECT * FROM events WHERE scenario_id = :scenario_id "
            "ORDER BY tick_day, intra_day_seq, event_type, viewer_id LIMIT :lim"
        )
        result = await self._s.execute(text(sql), {"scenario_id": scenario_id, "lim": limit})
        return [dict(r._mapping) for r in result]
