"""State persistence for the simulator.

Postgres is the canonical store.  When DATABASE_URL is unset (e.g. a small
Replit preview without Postgres provisioned), we fall back to Replit DB — a
managed key-value store that survives redeploys.  We NEVER fall back to local
SQLite: filesystem writes don't survive Replit Deployment redeploys.

Tables (rationale)
------------------
scenarios     — scenario manifests; written by Layer 6 synthesizer
events        — raw event log; written by substrate (M2)
observations  — every sanitizer flag from agentic layers (winning AND losing)
syntheses     — every synthesizer decision (the audit trail)
baselines     — extracted baselines after synthesizer commit (Layer 4)
params        — per-algorithm parameter overrides set at runtime (Layer 3)
evaluations   — interview evaluator output (Layer 10)

The "minimize the game of telephone" principle: we keep durable artifacts
(observations + syntheses) for every agent interaction, not just the final
chat output, so any decision can be audited and replayed.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

# JSONB on Postgres; portable JSON on every other dialect (SQLite for tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")

log = logging.getLogger(__name__)

metadata = MetaData()


# -----------------------------------------------------------------------------
# Schema definitions — kept here (single source of truth for tables that
# aren't owned by a layer).  The events table is owned by substrate/ and is
# defined again in substrate/schema.sql at M2; the definitions must match.
# -----------------------------------------------------------------------------

scenarios = Table(
    "scenarios",
    metadata,
    Column("scenario_id", String(64), primary_key=True),
    Column("name", String(256), nullable=False),
    Column("description", Text, nullable=True),
    Column("perturbations", JSONType, nullable=False),
    Column("horizon_days", Integer, nullable=False),
    Column("seed", BigInteger, nullable=False),
    Column("disputed", Integer, nullable=False, server_default=text("0")),
    Column("confidence", Float, nullable=True),
    Column("created_at", Float, nullable=False),
    # M15c — agentic-side fields populated by synthesize_scenario.
    # Nullable so M7-only writes (engine/scenario.py:save) keep working
    # without code changes.
    Column("natural_language_intent", Text, nullable=True),
    Column("time_horizon", String(8), nullable=True),
    Column("audience_filters", JSONType, nullable=True),
    Column("iterations_used", Integer, nullable=True, server_default=text("0")),
    Column("evaluator_verdict_json", JSONType, nullable=True),
)

events = Table(
    "events",
    metadata,
    Column("event_id", String(32), primary_key=True),
    Column("scenario_id", String(64), ForeignKey("scenarios.scenario_id"), nullable=False, index=True),
    Column("tick_day", Integer, nullable=False, index=True),
    Column("intra_day_seq", Integer, nullable=False),
    Column("timestamp", Float, nullable=False),
    Column("event_type", String(32), nullable=False, index=True),
    Column("viewer_id", String(64), nullable=True, index=True),
    Column("viewer_segment", String(32), nullable=True, index=True),
    Column("viewer_geo", String(8), nullable=True),
    Column("viewer_tenure_days", Integer, nullable=True),
    Column("creator_id", String(64), nullable=True),
    Column("creator_tier", String(16), nullable=True),
    Column("reel_id", String(64), nullable=True),
    Column("reel_duration_sec", Float, nullable=True),
    Column("reel_topic_cluster", String(32), nullable=True),
    Column("surface", String(16), nullable=True),
    Column("pool", String(16), nullable=True),
    Column("rank_score", Float, nullable=True),
    Column("watch_duration_sec", Float, nullable=True),
    Column("ad_impression", Integer, nullable=True),
    Column("ad_revenue_usd", Float, nullable=True),
    Column("payload", JSONType, nullable=True),
)

observations = Table(
    "observations",
    metadata,
    Column("observation_id", String(32), primary_key=True),
    Column("layer", String(8), nullable=False, index=True),
    Column("tool", String(64), nullable=False),
    Column("subject_id", String(128), nullable=False, index=True),  # what was being observed
    Column("kind", String(64), nullable=False),                     # flag kind / sanitizer category
    Column("severity", String(16), nullable=True),
    Column("evidence", JSONType, nullable=True),
    Column("trace_id", String(64), nullable=True),
    Column("created_at", Float, nullable=False),
)

syntheses = Table(
    "syntheses",
    metadata,
    Column("synthesis_id", String(32), primary_key=True),
    Column("layer", String(8), nullable=False, index=True),
    Column("subject_id", String(128), nullable=False, index=True),
    Column("decision", String(16), nullable=False),  # accept | reject | escalate
    Column("confidence", Float, nullable=False),
    Column("rationale", Text, nullable=True),
    Column("inputs", JSONType, nullable=True),          # references to observations
    Column("output", JSONType, nullable=True),          # the committed artifact
    Column("disputed", Integer, nullable=False, server_default=text("0")),
    Column("trace_id", String(64), nullable=True),
    Column("created_at", Float, nullable=False),
)

baselines = Table(
    "baselines",
    metadata,
    Column("baseline_id", String(64), primary_key=True),
    Column("metric_id", String(64), nullable=False, index=True),
    Column("value", Float, nullable=False),
    Column("unit", String(32), nullable=True),
    Column("period", String(32), nullable=True),
    Column("source", JSONType, nullable=False),
    Column("synthesis_id", String(32), ForeignKey("syntheses.synthesis_id"), nullable=False),
    Column("committed_at", Float, nullable=False),
)

params = Table(
    "params",
    metadata,
    Column("scope", String(64), primary_key=True),  # algorithm name
    Column("payload", JSONType, nullable=False),
    Column("updated_at", Float, nullable=False),
)

evaluations = Table(
    "evaluations",
    metadata,
    Column("evaluation_id", String(32), primary_key=True),
    Column("question_id", String(64), nullable=False, index=True),
    Column("answer_text", Text, nullable=False),
    Column("overall_score", Float, nullable=False),
    Column("payload", JSONType, nullable=False),
    Column("created_at", Float, nullable=False),
)


# -----------------------------------------------------------------------------
# Engine / session lifecycle
# -----------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        return None
    # Auto-upgrade postgres:// to async driver if the user pasted a sync URL.
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+asyncpg://", 1)
    elif raw.startswith("postgresql://") and "+asyncpg" not in raw:
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine

    url = _database_url()
    if url is None:
        raise RuntimeError(
            "DATABASE_URL is unset. Set Postgres for full functionality, or use "
            "ReplitDBStore for the key-value fallback path."
        )
    _engine = create_async_engine(url, pool_pre_ping=True, future=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as s:
        yield s


async def init_schema() -> None:
    """Create all tables.  Idempotent.  Used in tests and on first deploy."""
    eng = get_engine()
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)


# -----------------------------------------------------------------------------
# Replit DB fallback — key-value only, used when Postgres isn't provisioned
# -----------------------------------------------------------------------------

class ReplitDBStore:
    """Tiny key-value wrapper around Replit DB.

    Only used for non-relational state (e.g. session flags) when Postgres is
    not available.  Does NOT replicate the relational schema above; callers
    that need joins must use Postgres.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base = base_url or os.environ.get("REPLIT_DB_URL")
        if not self._base:
            raise RuntimeError("REPLIT_DB_URL is unset; cannot use ReplitDBStore")

    async def get(self, key: str) -> Any:
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._base}/{key}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            try:
                return json.loads(r.text)
            except json.JSONDecodeError:
                return r.text

    async def set(self, key: str, value: Any) -> None:
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.post(self._base or "", data={key: json.dumps(value)})
            r.raise_for_status()

    async def delete(self, key: str) -> None:
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.delete(f"{self._base}/{key}")
            if r.status_code not in (200, 204, 404):
                r.raise_for_status()
