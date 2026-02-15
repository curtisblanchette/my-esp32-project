"""
Cortex intelligence API — system status, baselines, rule suggestions, rules.

Phase 4+6 endpoints for monitoring and managing the adaptive learning system.
"""

from dataclasses import asdict
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..services.sqlite_client import SqliteClient
    from ..services.outcome_tracker import OutcomeTracker
    from ..services.cortex_memory import CortexMemory
    from ..services.rule_advisor import RuleAdvisor
    from ..services.websocket_server import WebSocketServer
    from ..services.decision_engine import DecisionEngine


class AdjustmentAction(BaseModel):
    action: str  # "approve" or "reject"


class RuleToggle(BaseModel):
    enabled: bool


def _serialize_rules(engine: "DecisionEngine") -> list[dict]:
    """Serialize in-memory rules to JSON-safe dicts."""
    result = []
    for rule in engine.rules:
        condition = asdict(rule.condition)
        action = asdict(rule.action)
        result.append({
            "name": rule.name,
            "description": rule.description,
            "enabled": rule.enabled,
            "condition": condition,
            "action": action,
            "modified": rule.name in engine.modified_rules,
        })
    return result


def create_cortex_router(
    sqlite: "SqliteClient",
    outcome_tracker: "OutcomeTracker",
    memory: "CortexMemory",
    rule_advisor: "RuleAdvisor",
    ws_server: "WebSocketServer | None" = None,
    engine: "DecisionEngine | None" = None,
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

    # ── Rules Endpoints (Phase 6: Nerve Center) ───────────────────────

    @r.get("/rules")
    async def get_rules():
        """List all in-memory rules with their current state."""
        if not engine:
            return {"ok": True, "rules": []}
        return {"ok": True, "rules": _serialize_rules(engine)}

    @r.patch("/rules/{rule_name}")
    async def toggle_rule(rule_name: str, body: RuleToggle):
        """Toggle a rule's enabled state (in-memory only)."""
        if not engine:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": "Decision engine not available"},
            )

        for rule in engine.rules:
            if rule.name == rule_name:
                rule.enabled = body.enabled
                # Broadcast updated rules
                if ws_server:
                    await ws_server.broadcast_rules(_serialize_rules(engine))
                return {"ok": True}

        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"Rule '{rule_name}' not found"},
        )

    return r
