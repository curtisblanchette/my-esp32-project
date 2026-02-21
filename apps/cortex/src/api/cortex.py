"""
Cortex intelligence API — grow profiles, goals, ecosystem health, and effects.
"""

import logging
import sqlite3
import time

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..services.sqlite_client import SqliteClient
    from ..services.websocket_server import WebSocketServer

logger = logging.getLogger(__name__)

VALID_STRATEGIES = {"precision", "balanced", "efficiency"}
VALID_PHASES = {"seedling", "veg", "flower", "late_flower", "dry", "cure"}
VALID_METRIC_TYPES = {"sensor", "derived", "relay_schedule"}


class ProfileBody(BaseModel):
    location: str
    name: str
    strategy: str = "balanced"
    phase: str | None = None
    phaseStart: str | None = None


class ProfileUpdate(BaseModel):
    name: str | None = None
    strategy: str | None = None
    phase: str | None = None
    phaseStart: str | None = None
    active: bool | None = None


class GoalBody(BaseModel):
    metric: str
    metricType: str = "sensor"
    phase: str | None = None
    rangeMin: float | None = None
    rangeMax: float | None = None
    tolerance: float = 0.0
    priority: float = 1.0
    schedule: dict | None = None
    timeWindow: dict | None = None


def create_cortex_router(
    sqlite: "SqliteClient",
    ws_server: "WebSocketServer | None" = None,
) -> APIRouter:
    r = APIRouter()

    @r.get("/status")
    async def get_status():
        """System intelligence overview."""
        return {"ok": True}

    # ── Grow Profiles ────────────────────────────────────────────────

    @r.get("/profiles")
    async def get_profiles():
        return {"ok": True, "profiles": sqlite.get_all_profiles()}

    @r.get("/profiles/{profile_id}")
    async def get_profile(profile_id: str):
        profile = sqlite.get_profile(profile_id)
        if not profile:
            return JSONResponse(status_code=404, content={"ok": False, "error": "Profile not found"})
        return {"ok": True, "profile": profile}

    @r.post("/profiles")
    async def create_profile(body: ProfileBody):
        if body.strategy not in VALID_STRATEGIES:
            return JSONResponse(status_code=400, content={"ok": False, "error": f"strategy must be one of {VALID_STRATEGIES}"})
        if body.phase and body.phase not in VALID_PHASES:
            return JSONResponse(status_code=400, content={"ok": False, "error": f"phase must be one of {VALID_PHASES}"})
        try:
            profile = sqlite.insert_profile(
                location=body.location,
                name=body.name,
                strategy=body.strategy,
                phase=body.phase,
                phase_start=body.phaseStart,
            )
        except sqlite3.IntegrityError:
            return JSONResponse(status_code=409, content={"ok": False, "error": f"Profile for location '{body.location}' already exists"})
        return {"ok": True, "profile": profile}

    @r.put("/profiles/{profile_id}")
    async def update_profile(profile_id: str, body: ProfileUpdate):
        if body.strategy and body.strategy not in VALID_STRATEGIES:
            return JSONResponse(status_code=400, content={"ok": False, "error": f"strategy must be one of {VALID_STRATEGIES}"})
        if body.phase and body.phase not in VALID_PHASES:
            return JSONResponse(status_code=400, content={"ok": False, "error": f"phase must be one of {VALID_PHASES}"})
        profile = sqlite.update_profile(
            profile_id,
            name=body.name,
            strategy=body.strategy,
            phase=body.phase,
            phase_start=body.phaseStart,
            active=body.active,
        )
        if not profile:
            return JSONResponse(status_code=404, content={"ok": False, "error": "Profile not found"})
        return {"ok": True, "profile": profile}

    @r.delete("/profiles/{profile_id}")
    async def delete_profile(profile_id: str):
        if not sqlite.delete_profile(profile_id):
            return JSONResponse(status_code=404, content={"ok": False, "error": "Profile not found"})
        return {"ok": True}

    # ── Goals ────────────────────────────────────────────────────────

    @r.get("/profiles/{profile_id}/goals")
    async def get_goals(profile_id: str, phase: str | None = Query(None)):
        if not sqlite.get_profile(profile_id):
            return JSONResponse(status_code=404, content={"ok": False, "error": "Profile not found"})
        goals = sqlite.get_goals_for_profile(profile_id, phase=phase)
        return {"ok": True, "goals": goals}

    @r.post("/profiles/{profile_id}/goals")
    async def create_goal(profile_id: str, body: GoalBody):
        if not sqlite.get_profile(profile_id):
            return JSONResponse(status_code=404, content={"ok": False, "error": "Profile not found"})
        if body.metricType not in VALID_METRIC_TYPES:
            return JSONResponse(status_code=400, content={"ok": False, "error": f"metricType must be one of {VALID_METRIC_TYPES}"})
        if body.phase and body.phase not in VALID_PHASES:
            return JSONResponse(status_code=400, content={"ok": False, "error": f"phase must be one of {VALID_PHASES}"})
        tw_on = body.timeWindow.get("onHour") if body.timeWindow else None
        tw_off = body.timeWindow.get("offHour") if body.timeWindow else None
        goal = sqlite.insert_goal(
            profile_id=profile_id,
            metric=body.metric,
            metric_type=body.metricType,
            phase=body.phase,
            range_min=body.rangeMin,
            range_max=body.rangeMax,
            tolerance=body.tolerance,
            priority=body.priority,
            schedule=body.schedule,
            time_window_on_hour=tw_on,
            time_window_off_hour=tw_off,
        )
        return {"ok": True, "goal": goal}

    @r.put("/goals/{goal_id}")
    async def update_goal(goal_id: str, body: GoalBody):
        if body.metricType not in VALID_METRIC_TYPES:
            return JSONResponse(status_code=400, content={"ok": False, "error": f"metricType must be one of {VALID_METRIC_TYPES}"})
        update_kwargs: dict[str, Any] = dict(
            metric=body.metric,
            metric_type=body.metricType,
            phase=body.phase,
            range_min=body.rangeMin,
            range_max=body.rangeMax,
            tolerance=body.tolerance,
            priority=body.priority,
            schedule=body.schedule,
        )
        if body.timeWindow is not None:
            update_kwargs["time_window_on_hour"] = body.timeWindow.get("onHour")
            update_kwargs["time_window_off_hour"] = body.timeWindow.get("offHour")
        elif body.timeWindow is None and "timeWindow" in (body.model_fields_set or set()):
            # Explicit None to clear the time window
            update_kwargs["time_window_on_hour"] = None
            update_kwargs["time_window_off_hour"] = None
        goal = sqlite.update_goal(goal_id, **update_kwargs)
        if not goal:
            return JSONResponse(status_code=404, content={"ok": False, "error": "Goal not found"})
        return {"ok": True, "goal": goal}

    @r.delete("/goals/{goal_id}")
    async def delete_goal(goal_id: str):
        if not sqlite.delete_goal(goal_id):
            return JSONResponse(status_code=404, content={"ok": False, "error": "Goal not found"})
        return {"ok": True}

    # ── Ecosystem Health ─────────────────────────────────────────────

    @r.get("/health/{location}")
    async def get_health(
        location: str,
        sinceMs: int = Query(default=0),
        untilMs: int | None = Query(default=None),
    ):
        if sinceMs == 0:
            sinceMs = int(time.time() * 1000) - 86_400_000  # default: last 24h
        records = sqlite.query_health(location, since_ms=sinceMs, until_ms=untilMs)
        latest = sqlite.get_latest_health(location)
        return {
            "ok": True,
            "latest": latest,
            "history": records,
        }

    # ── Effect Profiles ────────────────────────────────────────────

    @r.get("/effects")
    async def get_effects(
        deviceId: str | None = Query(default=None),
        actuator: str | None = Query(default=None),
        minSamples: int = Query(default=3),
    ):
        """Learned cross-variable actuator impact profiles."""
        effects = sqlite.get_effects(
            device_id=deviceId, actuator=actuator, min_samples=minSamples,
        )
        return {"ok": True, "effects": effects}

    return r
