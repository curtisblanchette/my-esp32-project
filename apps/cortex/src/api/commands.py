"""
Command routes: GET /api/commands, GET /api/commands/:id, POST /api/commands

Ported from apps/api/src/routes/commands.ts
"""

import time
import uuid

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from typing import Any


class CreateCommandRequest(BaseModel):
    deviceId: str
    location: str
    target: str
    action: str
    value: Any = None
    source: str = "dashboard"
    reason: str | None = None
    ttl: int | None = None


def create_commands_router(sqlite, ws_server) -> APIRouter:
    r = APIRouter()

    @r.post("")
    async def create_command(body: CreateCommandRequest, request: Request):
        try:
            mqtt = getattr(request.app.state, "mqtt", None)
            correlation_id = f"cmd-{uuid.uuid4().hex[:8]}"
            now = int(time.time() * 1000)

            envelope = {
                "v": 1, "ts": now, "correlationId": correlation_id,
                "source": body.source, "deviceId": body.deviceId, "location": body.location,
                "type": "command",
                "payload": {
                    "target": body.target, "action": body.action, "value": body.value,
                    "reason": body.reason, "ttl": body.ttl or 30000,
                },
            }

            if not mqtt or not mqtt.is_connected:
                return {"ok": False, "error": "MQTT client not connected"}

            mqtt.publish_json(f"home/{body.location}/{body.deviceId}/command", envelope)

            cmd = sqlite.insert_command(
                id=correlation_id, ts=now, device_id=body.deviceId,
                target=body.target, action=body.action, value=body.value,
                source=body.source, reason=body.reason, ttl=body.ttl,
            )

            return {"ok": True, "command": sqlite.command_to_dict(cmd)}
        except Exception as e:
            return {"ok": False, "error": f"Failed to send command: {e}"}

    @r.get("")
    async def list_commands(
        sinceMs: int | None = Query(None),
        untilMs: int | None = Query(None),
        deviceId: str | None = Query(None),
        status: str | None = Query(None),
        limit: int = Query(100),
    ):
        try:
            since = sinceMs if sinceMs is not None else int(time.time() * 1000) - 24 * 60 * 60 * 1000
            commands = sqlite.query_commands(
                since_ms=since, until_ms=untilMs,
                device_id=deviceId, status=status, limit=limit,
            )
            return {"ok": True, "commands": [sqlite.command_to_dict(c) for c in commands]}
        except Exception as e:
            return {"ok": False, "error": f"Failed to query commands: {e}"}

    @r.get("/{command_id}")
    async def get_command(command_id: str):
        try:
            cmd = sqlite.get_command(command_id)
            if not cmd:
                return {"ok": False, "error": "Command not found"}
            return {"ok": True, "command": sqlite.command_to_dict(cmd)}
        except Exception as e:
            return {"ok": False, "error": f"Failed to fetch command: {e}"}

    return r
