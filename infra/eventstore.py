"""Redis-backed EventStore for streamable-http resumability.

The MCP streamable-http transport uses Last-Event-ID for resumability: when a
client reconnects after a network blip, it sends the last event id it saw and
the server replays everything after that.  On Replit, where Reserved VMs can
recycle, we MUST NOT keep this state in process memory — Redis is the source
of truth.

Storage shape
-------------
For each session_id, we keep a Redis stream:
    key:  mcp:events:{session_id}
    type: stream
    entry: { event_type, payload_json, ts }

Event ids are Redis stream ids ("<ms>-<seq>") and exposed verbatim as the
Last-Event-ID header value so the client can hand them back unmodified.

TTL: streams expire after STREAM_TTL_SECONDS of inactivity.  This is a
backstop — orderly shutdown should call drop(session_id).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import redis.asyncio as redis

log = logging.getLogger(__name__)

# 24h: long enough to survive overnight client disconnects, short enough that
# abandoned sessions don't accumulate unbounded.
STREAM_TTL_SECONDS = 24 * 60 * 60


@dataclass
class StoredEvent:
    event_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


class RedisEventStore:
    """Append-only event log keyed by session id.

    All operations are async because FastMCP's transport calls happen on the
    asyncio event loop and we don't want to block it on Redis round-trips.
    """

    def __init__(self, client: redis.Redis, *, key_prefix: str = "mcp:events") -> None:
        self._r = client
        self._prefix = key_prefix

    # ---- factory ----------------------------------------------------------
    @classmethod
    def from_env(cls) -> "RedisEventStore":
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        client = redis.from_url(url, decode_responses=True)
        return cls(client)

    # ---- internal ---------------------------------------------------------
    def _key(self, session_id: str) -> str:
        return f"{self._prefix}:{session_id}"

    # ---- public API expected by streamable-http transport -----------------
    async def append(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Append an event and return its assigned event id."""
        key = self._key(session_id)
        fields = {
            "event_type": event_type,
            "payload_json": json.dumps(payload or {}, separators=(",", ":")),
        }
        event_id = await self._r.xadd(key, fields)
        await self._r.expire(key, STREAM_TTL_SECONDS)
        return event_id

    async def since(
        self,
        session_id: str,
        last_event_id: str | None,
        *,
        limit: int = 1000,
    ) -> list[StoredEvent]:
        """Return events strictly after last_event_id (inclusive of nothing).

        last_event_id=None means "from the beginning".  Result is capped at
        `limit` to prevent unbounded replay; callers should page if needed.
        """
        key = self._key(session_id)
        start = "-" if last_event_id is None else f"({last_event_id}"
        entries = await self._r.xrange(key, min=start, max="+", count=limit)
        out: list[StoredEvent] = []
        for entry_id, fields in entries:
            payload_raw = fields.get("payload_json", "{}")
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                log.warning("malformed payload at %s: %s", entry_id, payload_raw)
                payload = {}
            out.append(
                StoredEvent(
                    event_id=entry_id,
                    event_type=fields.get("event_type", ""),
                    payload=payload,
                )
            )
        return out

    async def stream(
        self,
        session_id: str,
        last_event_id: str | None = None,
    ) -> AsyncIterator[StoredEvent]:
        """Async generator yielding events as they're appended (XREAD BLOCK).

        Used by the long-lived SSE/streamable-http response loop.
        """
        cursor = last_event_id or "$"
        key = self._key(session_id)
        while True:
            resp = await self._r.xread({key: cursor}, block=15_000, count=64)
            if not resp:
                continue
            for _stream_key, entries in resp:
                for entry_id, fields in entries:
                    cursor = entry_id
                    try:
                        payload = json.loads(fields.get("payload_json", "{}"))
                    except json.JSONDecodeError:
                        payload = {}
                    yield StoredEvent(
                        event_id=entry_id,
                        event_type=fields.get("event_type", ""),
                        payload=payload,
                    )

    async def drop(self, session_id: str) -> None:
        await self._r.delete(self._key(session_id))

    async def close(self) -> None:
        await self._r.aclose()
