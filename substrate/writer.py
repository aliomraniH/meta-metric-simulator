"""EventWriter — the only path that inserts into the events table.

Validation is intentionally light: the substrate trusts its callers (the
algorithms in Layer 2 are pure functions and cannot inject arbitrary
shapes) but enforces three invariants:

  1. event_type ∈ EVENT_TYPES.
  2. event_id is a 26-char ULID — generated here if absent.
  3. required-fields-per-event-type from docs/EVENT_SCHEMA.md.

Batched flush: write_batch() groups rows into a single INSERT — the
simulator emits ~100k events per scenario, so per-row commits would be
prohibitively slow on Replit Postgres.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Mapping

import ulid
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from infra.db import events as events_table

log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Event type contract — keep in lockstep with docs/EVENT_SCHEMA.md
# -----------------------------------------------------------------------------

EVENT_TYPES: frozenset[str] = frozenset({
    "impression",
    "watch",
    "skip",
    "like",
    "send",
    "save",
    "comment",
    "replay",
    "view_end",
    "guardrail_fired",
    "ad_impression",
    "creator_post",
    "scenario_perturbation",
})

# Required scalar fields per event type (excluding scenario_id, tick_day,
# intra_day_seq, timestamp — those are required for ALL events).
REQUIRED_BY_TYPE: dict[str, tuple[str, ...]] = {
    "impression":            ("viewer_id", "viewer_segment", "reel_id", "creator_id", "surface", "pool", "rank_score"),
    "watch":                 ("viewer_id", "reel_id", "watch_duration_sec"),
    "skip":                  ("viewer_id", "reel_id", "watch_duration_sec"),
    "like":                  ("viewer_id", "reel_id"),
    "send":                  ("viewer_id", "reel_id"),
    "save":                  ("viewer_id", "reel_id"),
    "comment":               ("viewer_id", "reel_id"),
    "replay":                ("viewer_id", "reel_id", "watch_duration_sec"),
    "view_end":              ("viewer_id", "reel_id", "watch_duration_sec"),
    "guardrail_fired":       (),  # all required content lives in payload
    "ad_impression":         ("viewer_id", "viewer_segment", "ad_impression", "ad_revenue_usd"),
    "creator_post":          ("creator_id", "creator_tier", "reel_id", "reel_duration_sec", "reel_topic_cluster"),
    "scenario_perturbation": (),  # payload-driven
}

ALWAYS_REQUIRED: tuple[str, ...] = ("scenario_id", "tick_day", "intra_day_seq", "timestamp", "event_type")


class EventValidationError(ValueError):
    """Raised when an event row fails the substrate's invariants."""


# -----------------------------------------------------------------------------
# Writer
# -----------------------------------------------------------------------------

class EventWriter:
    """Insert events into the canonical log.

    Usage:
        async with session() as s:
            w = EventWriter(s)
            await w.write_batch(rows)
            await s.commit()
    """

    def __init__(self, session: AsyncSession, *, batch_size: int = 1000) -> None:
        self._s = session
        self._batch_size = batch_size

    # ---- single-row API (test helper, not the hot path) ------------------
    async def write(self, event: Mapping[str, Any]) -> str:
        prepared = _prepare(event)
        await self._s.execute(insert(events_table), [prepared])
        return prepared["event_id"]

    # ---- batch API (the hot path) ----------------------------------------
    async def write_batch(self, events: Iterable[Mapping[str, Any]]) -> int:
        prepared = [_prepare(e) for e in events]
        if not prepared:
            return 0
        # Chunk into INSERTs of `batch_size`; asyncpg handles parameterisation.
        n = 0
        for i in range(0, len(prepared), self._batch_size):
            chunk = prepared[i : i + self._batch_size]
            await self._s.execute(insert(events_table), chunk)
            n += len(chunk)
        log.debug("wrote %d events", n)
        return n


# -----------------------------------------------------------------------------
# Validation + ULID assignment
# -----------------------------------------------------------------------------

# All columns the events table accepts — used to drop stray keys before insert.
_COLUMNS: frozenset[str] = frozenset({
    "event_id", "scenario_id", "tick_day", "intra_day_seq", "timestamp",
    "event_type", "viewer_id", "viewer_segment", "viewer_geo",
    "viewer_tenure_days", "creator_id", "creator_tier", "reel_id",
    "reel_duration_sec", "reel_topic_cluster", "surface", "pool",
    "rank_score", "watch_duration_sec", "ad_impression", "ad_revenue_usd",
    "payload",
})


def _prepare(event: Mapping[str, Any]) -> dict[str, Any]:
    """Validate, fill defaults, drop unknown keys.  Returns the row to insert.

    Every column in `_COLUMNS` is present on the returned dict (with None
    when unspecified) so batched INSERTs compile against a uniform shape.
    """
    # Start with every column as None so the row's key set is uniform —
    # SQLAlchemy compiles a multi-row INSERT against the first row's keys
    # and fails if subsequent rows are missing any of them.
    row: dict[str, Any] = {col: None for col in _COLUMNS}
    for k, v in event.items():
        if k in _COLUMNS:
            row[k] = v

    # Always-required fields.
    for fld in ALWAYS_REQUIRED:
        if row.get(fld) is None:
            raise EventValidationError(f"event missing required field {fld!r}")

    et = row["event_type"]
    if et not in EVENT_TYPES:
        raise EventValidationError(
            f"unknown event_type {et!r}; allowed: {sorted(EVENT_TYPES)}"
        )

    # Per-type required fields.
    for fld in REQUIRED_BY_TYPE.get(et, ()):
        if row.get(fld) is None:
            raise EventValidationError(
                f"event_type={et!r} requires field {fld!r}"
            )

    # ULID generation if not supplied.  ulid.new() returns a ULID instance;
    # str() yields the 26-char Crockford-base32 form we store.
    if not row.get("event_id"):
        row["event_id"] = str(ulid.new())
    elif not isinstance(row["event_id"], str) or len(row["event_id"]) != 26:
        raise EventValidationError(
            f"event_id must be a 26-char ULID string, got {row['event_id']!r}"
        )

    # Type coercions Postgres won't auto-apply.
    if isinstance(row.get("ad_impression"), bool):
        row["ad_impression"] = int(row["ad_impression"])

    return row


def now_seconds() -> float:
    """Wall-clock-anchored helper — kept here so test code doesn't import time."""
    return time.time()
