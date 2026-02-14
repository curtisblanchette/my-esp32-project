"""
Relay routes: GET/POST/PATCH/DELETE /api/devices/:deviceId/relays/:id

Ported from apps/api/src/routes/relays.ts
"""

import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel


class RelayStateRequest(BaseModel):
    state: bool | None = None


class RelayNameRequest(BaseModel):
    name: str | None = None


def create_relays_router(sqlite, ws_server) -> APIRouter:
    r = APIRouter()

    @r.get("")
    async def list_relays(deviceId: str):
        try:
            actuators = sqlite.get_device_actuators(deviceId)
            relays = [
                {
                    "id": a.id,
                    "name": a.custom_name or a.name or a.id,
                    "type": a.type or "switch",
                    "state": a.state if a.state is not None else False,
                    "updatedAt": int(time.time() * 1000),
                    "deviceId": a.device_id,
                    "location": a.location,
                    "deviceOnline": a.device_online,
                }
                for a in actuators
            ]
            return {"ok": True, "relays": relays}
        except Exception as e:
            return {"ok": False, "error": f"Failed to fetch relays: {e}"}

    @r.get("/{relay_id}")
    async def get_relay(deviceId: str, relay_id: str):
        try:
            actuators = sqlite.get_device_actuators(deviceId)
            actuator = next((a for a in actuators if a.id == relay_id), None)
            if not actuator:
                return {"ok": False, "error": "Relay not found"}
            return {
                "ok": True,
                "relay": {
                    "id": actuator.id,
                    "name": actuator.custom_name or actuator.name or actuator.id,
                    "type": actuator.type or "switch",
                    "state": actuator.state if actuator.state is not None else False,
                    "deviceId": actuator.device_id,
                    "location": actuator.location,
                    "deviceOnline": actuator.device_online,
                },
            }
        except Exception as e:
            return {"ok": False, "error": f"Failed to fetch relay: {e}"}

    @r.post("/{relay_id}")
    async def control_relay(deviceId: str, relay_id: str, body: RelayStateRequest, request: Request):
        try:
            actuators = sqlite.get_device_actuators(deviceId)
            actuator = next((a for a in actuators if a.id == relay_id), None)
            if not actuator:
                return {"ok": False, "error": "Relay not found"}

            relay_name = actuator.custom_name or actuator.name or relay_id
            location = actuator.location

            mqtt = getattr(request.app.state, "mqtt", None)

            if actuator.type == "momentary":
                # Momentary: send pulse
                correlation_id = f"cmd-{uuid.uuid4().hex[:8]}"
                now = int(time.time() * 1000)

                envelope = {
                    "v": 1, "ts": now, "correlationId": correlation_id,
                    "source": "dashboard", "deviceId": deviceId, "location": location,
                    "type": "command",
                    "payload": {
                        "target": relay_id, "action": "pulse", "value": True,
                        "reason": f"Relay {relay_name} pulsed via dashboard", "ttl": 30000,
                    },
                }

                if not mqtt or not mqtt.is_connected:
                    return {"ok": False, "error": "MQTT client not connected"}

                mqtt.publish_json(f"home/{location}/{deviceId}/command", envelope)
                cmd = sqlite.insert_command(
                    id=correlation_id, ts=now, device_id=deviceId,
                    target=relay_id, action="pulse", value=True,
                    source="dashboard", reason=f"Relay {relay_name} pulsed via dashboard",
                )
                await ws_server.broadcast_command(sqlite.command_to_dict(cmd))

                return {
                    "ok": True, "correlationId": correlation_id,
                    "relay": {"id": relay_id, "name": relay_name, "type": "momentary",
                              "state": False, "deviceId": deviceId, "location": location},
                }
            else:
                # Switch: set state
                if body.state is None:
                    return {"ok": False, "error": "state must be a boolean"}

                correlation_id = f"cmd-{uuid.uuid4().hex[:8]}"
                now = int(time.time() * 1000)
                reason = f"Relay {relay_name} set to {'ON' if body.state else 'OFF'} via dashboard"

                envelope = {
                    "v": 1, "ts": now, "correlationId": correlation_id,
                    "source": "dashboard", "deviceId": deviceId, "location": location,
                    "type": "command",
                    "payload": {
                        "target": relay_id, "action": "set", "value": body.state,
                        "reason": reason, "ttl": 30000,
                    },
                }

                if not mqtt or not mqtt.is_connected:
                    return {"ok": False, "error": "MQTT client not connected"}

                mqtt.publish_json(f"home/{location}/{deviceId}/command", envelope)
                cmd = sqlite.insert_command(
                    id=correlation_id, ts=now, device_id=deviceId,
                    target=relay_id, action="set", value=body.state,
                    source="dashboard", reason=reason,
                )
                await ws_server.broadcast_command(sqlite.command_to_dict(cmd))

                sqlite.update_actuator_state(deviceId, relay_id, body.state)

                return {
                    "ok": True, "correlationId": correlation_id,
                    "relay": {"id": relay_id, "name": relay_name, "state": body.state,
                              "deviceId": deviceId, "location": location},
                }
        except Exception as e:
            return {"ok": False, "error": f"Failed to set relay state: {e}"}

    @r.patch("/{relay_id}")
    async def update_relay_name(deviceId: str, relay_id: str, body: RelayNameRequest):
        try:
            actuators = sqlite.get_device_actuators(deviceId)
            actuator = next((a for a in actuators if a.id == relay_id), None)
            if not actuator:
                return {"ok": False, "error": "Relay not found"}

            if body.name is not None:
                if not sqlite.update_actuator_name(deviceId, relay_id, body.name):
                    return {"ok": False, "error": "Failed to update relay name"}

            device = sqlite.get_device(deviceId)
            updated = next(
                (a for a in (device.capabilities.actuators if device else []) if a.id == relay_id),
                None,
            )

            return {
                "ok": True,
                "relay": {
                    "id": relay_id,
                    "name": (device.actuator_names.get(relay_id) if device else None)
                           or (updated.name if updated else relay_id),
                    "state": updated.state if updated else False,
                    "deviceId": deviceId,
                    "location": device.location if device else actuator.location,
                    "deviceOnline": device.online if device else False,
                },
            }
        except Exception as e:
            return {"ok": False, "error": f"Failed to update relay: {e}"}

    @r.delete("/{relay_id}")
    async def delete_relay_name(deviceId: str, relay_id: str):
        try:
            actuators = sqlite.get_device_actuators(deviceId)
            actuator = next((a for a in actuators if a.id == relay_id), None)
            if not actuator:
                return {"ok": False, "error": "Relay not found"}

            sqlite.remove_actuator_name(actuator.device_id, relay_id)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"Failed to delete relay: {e}"}

    return r
