"""
Event routes: GET /api/events

Ported from apps/api/src/routes/events.ts
"""

import time

from fastapi import APIRouter, Query


def create_events_router(sqlite) -> APIRouter:
    r = APIRouter()

    @r.get("")
    async def list_events(
        sinceMs: int | None = Query(None),
        untilMs: int | None = Query(None),
        deviceId: str | None = Query(None),
        eventType: str | None = Query(None),
        limit: int = Query(100),
    ):
        try:
            since = sinceMs if sinceMs is not None else int(time.time() * 1000) - 24 * 60 * 60 * 1000
            events = sqlite.query_events(
                since_ms=since, until_ms=untilMs,
                device_id=deviceId, event_type=eventType, limit=limit,
            )
            return {"ok": True, "events": [sqlite.event_to_dict(e) for e in events]}
        except Exception as e:
            return {"ok": False, "error": f"Failed to query events: {e}"}

    return r
