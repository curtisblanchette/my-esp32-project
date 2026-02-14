from dataclasses import dataclass
from typing import Any


@dataclass
class Reading:
    """A single sensor reading."""
    id: str
    value: float | str
    unit: str | None = None


@dataclass
class TelemetryMessage:
    """Telemetry message from a device."""
    version: int
    ts: int
    device_id: str
    location: str
    readings: list[Reading]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TelemetryMessage":
        """Parse a telemetry message from MQTT payload."""
        payload = data.get("payload", {})
        readings = [
            Reading(
                id=r.get("id", ""),
                value=r.get("value"),
                unit=r.get("unit")
            )
            for r in payload.get("readings", [])
        ]
        return cls(
            version=data.get("v", 1),
            ts=data.get("ts", 0),
            device_id=data.get("deviceId", ""),
            location=data.get("location", ""),
            readings=readings
        )

    def get_reading(self, sensor_id: str) -> Reading | None:
        """Get a specific reading by sensor ID."""
        for r in self.readings:
            if r.id == sensor_id:
                return r
        return None
