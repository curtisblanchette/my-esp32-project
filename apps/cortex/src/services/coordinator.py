"""
Coordinator — cross-device state provider for the decision engine.

Phase 5: Provides methods to query sensor readings across multiple devices,
enabling rules with scope: any/all/<device_id> and target_scope: all/<device_id>.

Lightweight: references existing WebSocketServer._latest_by_device and
SqliteClient device registry. Does not store its own data.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .websocket_server import WebSocketServer
    from .sqlite_client import SqliteClient

logger = logging.getLogger(__name__)

class Coordinator:
    """Cross-device state provider for multi-device rule evaluation."""

    def __init__(self, ws_server: "WebSocketServer", sqlite: "SqliteClient"):
        self._ws = ws_server
        self._sqlite = sqlite

    def get_online_device_ids(self) -> list[str]:
        """Return IDs of all currently online devices."""
        devices = self._sqlite.get_online_devices()
        return [d.id for d in devices]

    def get_latest_reading(self, device_id: str, sensor_id: str) -> float | None:
        """Get latest sensor value for a specific device.

        Reads from ws_server._latest_by_device which is populated on
        every telemetry message. Returns None if device has no data.
        """
        latest = self._ws.get_latest_by_device(device_id)
        if not latest:
            return None

        # Read from generic readings dict
        readings = latest.get("readings", {})
        value = readings.get(sensor_id)

        if isinstance(value, (int, float)):
            return float(value)
        return None

    def get_all_latest_readings(self, sensor_id: str) -> dict[str, float]:
        """Get latest reading for a sensor across ALL online devices.

        Returns: {device_id: value} for every online device that has data.
        """
        online_ids = set(self.get_online_device_ids())
        readings_out: dict[str, float] = {}

        for device_id, latest in self._ws.get_all_latest_by_device().items():
            if device_id not in online_ids:
                continue
            readings = latest.get("readings", {})
            value = readings.get(sensor_id)
            if isinstance(value, (int, float)):
                readings_out[device_id] = float(value)

        return readings_out

    def device_has_actuator(self, device_id: str, actuator_id: str) -> bool:
        """Check if a device has a specific actuator."""
        device = self._sqlite.get_device(device_id)
        if not device:
            return False
        return any(a.id == actuator_id for a in device.capabilities.actuators)

    def get_devices_with_actuator(self, actuator_id: str) -> list[tuple[str, str]]:
        """Return (device_id, location) pairs for all online devices with a given actuator."""
        devices = self._sqlite.get_online_devices()
        results: list[tuple[str, str]] = []
        for device in devices:
            if any(a.id == actuator_id for a in device.capabilities.actuators):
                results.append((device.id, device.location))
        return results
