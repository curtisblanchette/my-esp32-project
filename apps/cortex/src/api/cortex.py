"""
Cortex intelligence API — system status, baselines, rule suggestions.

Phase 4 endpoints for monitoring and managing the adaptive learning system.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..services.sqlite_client import SqliteClient
    from ..services.outcome_tracker import OutcomeTracker
    from ..services.cortex_memory import CortexMemory
    from ..services.rule_advisor import RuleAdvisor
    from ..services.websocket_server import WebSocketServer


class AdjustmentAction(BaseModel):
    action: str  # "approve" or "reject"


def create_cortex_router(
    sqlite: "SqliteClient",
    outcome_tracker: "OutcomeTracker",
    memory: "CortexMemory",
    rule_advisor: "RuleAdvisor",
    ws_server: "WebSocketServer | None" = None,
) -> APIRouter:
    r = APIRouter()

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

    return r
