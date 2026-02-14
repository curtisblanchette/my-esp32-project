"""
Observation routes: POST /api/observations

Allows humans to log events that sensors can't detect (mold, pests, wilting, etc.).
Observations are stored as events with event_type="observation" and source="human".
"""

import time

from fastapi import APIRouter
from pydantic import BaseModel

OBSERVATION_CATEGORIES = [
    # Plant health
    "mold", "powdery_mildew", "pests", "needs_water", "overwatered",
    "nutrient_deficiency", "wilting", "leaf_damage", "root_rot",
    "harvest_ready",
    # Equipment / maintenance
    "filter_changed", "sensor_replaced", "device_moved",
    # Utility
    "high_bill_power", "high_bill_gas",
    # Catch-all
    "general",
]


class ObservationRequest(BaseModel):
    deviceId: str
    category: str
    notes: str | None = None


def create_observations_router(sqlite, ws_server) -> APIRouter:
    r = APIRouter()

    @r.post("")
    async def log_observation(body: ObservationRequest):
        if body.category not in OBSERVATION_CATEGORIES:
            return {"ok": False, "error": f"Invalid category: {body.category}. Must be one of: {', '.join(OBSERVATION_CATEGORIES)}"}

        device = sqlite.get_device(body.deviceId)
        if not device:
            return {"ok": False, "error": f"Unknown device: {body.deviceId}"}

        if body.category == "general" and not (body.notes and body.notes.strip()):
            return {"ok": False, "error": "General observations require notes"}

        notes = body.notes.strip() if body.notes and body.notes.strip() else None

        event = sqlite.insert_event(
            ts=int(time.time() * 1000),
            device_id=body.deviceId,
            event_type="observation",
            payload={"category": body.category, "notes": notes},
            source="human",
        )

        event_dict = sqlite.event_to_dict(event)
        await ws_server.broadcast_event(event_dict)

        return {"ok": True, "event": event_dict}

    return r
