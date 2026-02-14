"""
Device routes: GET /api/devices, GET /api/devices/:id, PUT /api/devices/order, actuators

Ported from apps/api/src/routes/devices.ts
"""

from fastapi import APIRouter
from pydantic import BaseModel


class ReorderRequest(BaseModel):
    order: list[str]


def create_devices_router(sqlite, ws_server) -> APIRouter:
    r = APIRouter()

    @r.put("/order")
    async def reorder_devices(body: ReorderRequest):
        try:
            orders = [{"id": id, "order": idx} for idx, id in enumerate(body.order)]
            sqlite.reorder_devices(orders)
            await ws_server.broadcast_devices(sqlite)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"Failed to reorder devices: {e}"}

    @r.get("")
    async def list_devices():
        try:
            devices = sqlite.get_all_devices()
            return {"ok": True, "devices": [d.to_dict() for d in devices]}
        except Exception as e:
            return {"ok": False, "error": f"Failed to fetch devices: {e}"}

    @r.get("/{device_id}")
    async def get_device(device_id: str):
        try:
            device = sqlite.get_device(device_id)
            if not device:
                return {"ok": False, "error": "Device not found"}
            return {"ok": True, "device": device.to_dict()}
        except Exception as e:
            return {"ok": False, "error": f"Failed to fetch device: {e}"}

    @r.get("/{device_id}/actuators")
    async def get_device_actuators(device_id: str):
        try:
            device = sqlite.get_device(device_id)
            if not device:
                return {"ok": False, "error": "Device not found"}
            actuators = sqlite.get_device_actuators(device_id)
            return {"ok": True, "actuators": [a.to_dict() for a in actuators]}
        except Exception as e:
            return {"ok": False, "error": f"Failed to fetch device actuators: {e}"}

    return r
