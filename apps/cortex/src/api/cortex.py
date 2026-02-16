"""
Cortex intelligence API — system status, baselines, rule suggestions, rules CRUD.

Phase 4+6+7 endpoints for monitoring and managing the adaptive learning system.
"""

import logging
import sqlite3

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..services.sqlite_client import SqliteClient
    from ..services.outcome_tracker import OutcomeTracker
    from ..services.cortex_memory import CortexMemory
    from ..services.rule_advisor import RuleAdvisor
    from ..services.websocket_server import WebSocketServer
    from ..services.decision_engine import DecisionEngine

logger = logging.getLogger(__name__)

VALID_OPERATORS = {">", "<", ">=", "<=", "==", "!="}
VALID_TRENDS = {"rising", "falling", "stable"}
VALID_FORECASTS = {"will_exceed", "will_drop_below"}


class AdjustmentAction(BaseModel):
    action: str  # "approve" or "reject"


class RuleToggle(BaseModel):
    enabled: bool


class RuleBody(BaseModel):
    name: str
    description: str
    condition: dict[str, Any]
    action: dict[str, Any]
    enabled: bool = True


def _validate_rule(condition: dict, action: dict) -> str | None:
    """Validate rule condition/action fields. Returns error message or None."""
    # Required condition fields
    if not condition.get("sensor"):
        return "condition.sensor is required"
    if condition.get("operator") not in VALID_OPERATORS:
        return f"condition.operator must be one of {VALID_OPERATORS}"
    if "threshold" not in condition:
        return "condition.threshold is required"

    # Required action fields
    if not action.get("target"):
        return "action.target is required"
    if not action.get("action"):
        return "action.action is required"
    if not action.get("reason"):
        return "action.reason is required"

    # Optional field validation
    if condition.get("trend") and condition["trend"] not in VALID_TRENDS:
        return f"condition.trend must be one of {VALID_TRENDS}"
    if condition.get("forecast") and condition["forecast"] not in VALID_FORECASTS:
        return f"condition.forecast must be one of {VALID_FORECASTS}"
    if condition.get("forecast") and condition.get("forecast_threshold") is None:
        return "condition.forecast_threshold is required when forecast is set"

    return None


def create_cortex_router(
    sqlite: "SqliteClient",
    outcome_tracker: "OutcomeTracker",
    memory: "CortexMemory",
    rule_advisor: "RuleAdvisor",
    ws_server: "WebSocketServer | None" = None,
    engine: "DecisionEngine | None" = None,
) -> APIRouter:
    r = APIRouter()

    async def _broadcast_rules():
        """Broadcast current rules from DB to all WebSocket clients."""
        if ws_server:
            all_rules = sqlite.get_all_rules()
            await ws_server.broadcast_rules(all_rules)

    @r.get("/status")
    async def get_status():
        """System intelligence overview."""
        # Outcome stats
        outcomes = outcome_tracker.query_outcomes(limit=100)
        total_outcomes = len(outcomes)
        avg_eff = 0.0
        success_rate = 0.0
        if outcomes:
            scores = [o["effectiveness"] for o in outcomes]
            avg_eff = round(sum(scores) / len(scores), 3)
            success_rate = round(sum(1 for s in scores if s > 0.1) / len(scores), 3)

        # Baseline stats
        devices = sqlite.get_all_devices()
        total_baselines = 0
        sensors_tracked = set()
        for device in devices:
            baselines = memory.get_all_baselines(device.id)
            total_baselines += len(baselines)
            for b in baselines:
                sensors_tracked.add(f"{device.id}:{b['metric']}")

        # Suggestion stats
        suggestion_counts = sqlite.count_suggestions_by_status()

        return {
            "ok": True,
            "outcomes": {
                "total": total_outcomes,
                "avgEffectiveness": avg_eff,
                "successRate": success_rate,
            },
            "baselines": {
                "total": total_baselines,
                "sensorsTracked": len(sensors_tracked),
            },
            "suggestions": {
                "pending": suggestion_counts.get("pending", 0),
                "applied": suggestion_counts.get("applied", 0),
                "rejected": suggestion_counts.get("rejected", 0),
                "total": sum(suggestion_counts.values()),
            },
            "lastAdvisorRun": rule_advisor.last_run_ts,
        }

    @r.get("/baselines/{device_id}")
    async def get_baselines(device_id: str):
        """Learned baselines for a device."""
        baselines = memory.get_all_baselines(device_id)
        return {"ok": True, "baselines": baselines}

    @r.get("/adjustments")
    async def get_adjustments(status: str | None = Query(None)):
        """Pending and historical rule suggestions."""
        suggestions = sqlite.get_suggestions(status=status)
        return {"ok": True, "adjustments": suggestions}

    @r.post("/adjustments/{suggestion_id}")
    async def resolve_adjustment(suggestion_id: str, body: AdjustmentAction):
        """Approve or reject a suggestion."""
        if body.action == "approve":
            ok = rule_advisor.apply_suggestion(suggestion_id)
        elif body.action == "reject":
            ok = rule_advisor.reject_suggestion(suggestion_id)
        else:
            return {"ok": False, "error": "action must be 'approve' or 'reject'"}

        if not ok:
            return {"ok": False, "error": "Suggestion not found or not in pending state"}

        # Broadcast updated suggestions to all WebSocket clients
        if ws_server:
            all_suggestions = sqlite.get_suggestions(limit=20)
            await ws_server.broadcast_suggestions(all_suggestions)

        return {"ok": True}

    @r.post("/advisor/run")
    async def run_advisor():
        """Manually trigger the Rule Advisor."""
        import asyncio
        suggestions = await asyncio.to_thread(rule_advisor.analyze)
        if ws_server:
            all_suggestions = sqlite.get_suggestions(limit=20)
            await ws_server.broadcast_suggestions(all_suggestions)
        return {
            "ok": True,
            "suggestions": suggestions or [],
            "count": len(suggestions) if suggestions else 0,
        }

    # ── Rules CRUD Endpoints ─────────────────────────────────────────

    @r.get("/rules")
    async def get_rules():
        """List all rules from the database."""
        return {"ok": True, "rules": sqlite.get_all_rules()}

    @r.post("/rules")
    async def create_rule(body: RuleBody):
        """Create a new rule."""
        error = _validate_rule(body.condition, body.action)
        if error:
            return JSONResponse(status_code=400, content={"ok": False, "error": error})

        try:
            rule = sqlite.insert_rule(
                name=body.name,
                description=body.description,
                condition=body.condition,
                action=body.action,
                enabled=body.enabled,
                source="user",
            )
        except sqlite3.IntegrityError:
            return JSONResponse(
                status_code=409,
                content={"ok": False, "error": f"Rule name '{body.name}' already exists"},
            )

        # Reload engine from DB
        if engine:
            engine.reload_from_sqlite(sqlite)

        await _broadcast_rules()
        return {"ok": True, "rule": rule}

    @r.put("/rules/{rule_id}")
    async def update_rule(rule_id: str, body: RuleBody):
        """Update a rule by id."""
        error = _validate_rule(body.condition, body.action)
        if error:
            return JSONResponse(status_code=400, content={"ok": False, "error": error})

        try:
            rule = sqlite.update_rule(
                rule_id=rule_id,
                name=body.name,
                description=body.description,
                condition=body.condition,
                action=body.action,
                enabled=body.enabled,
            )
        except sqlite3.IntegrityError:
            return JSONResponse(
                status_code=409,
                content={"ok": False, "error": f"Rule name '{body.name}' already exists"},
            )

        if rule is None:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": f"Rule '{rule_id}' not found"},
            )

        # Reload engine from DB
        if engine:
            engine.reload_from_sqlite(sqlite)

        await _broadcast_rules()
        return {"ok": True, "rule": rule}

    @r.delete("/rules/{rule_id}")
    async def delete_rule(rule_id: str):
        """Delete a rule by id. Cascade deletes associated suggestions."""
        deleted = sqlite.delete_rule(rule_id)
        if not deleted:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": f"Rule '{rule_id}' not found"},
            )

        # Reload engine from DB
        if engine:
            engine.reload_from_sqlite(sqlite)

        await _broadcast_rules()
        return {"ok": True}

    @r.patch("/rules/{rule_id}")
    async def toggle_rule(rule_id: str, body: RuleToggle):
        """Toggle a rule's enabled state (persists to DB)."""
        updated = sqlite.update_rule_enabled(rule_id, body.enabled)
        if not updated:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": f"Rule '{rule_id}' not found"},
            )

        # Update in-memory engine
        if engine:
            engine.reload_from_sqlite(sqlite)

        await _broadcast_rules()
        return {"ok": True}

    return r
