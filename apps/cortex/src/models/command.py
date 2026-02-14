from dataclasses import dataclass
from typing import Any
import uuid
import time


@dataclass
class Command:
    """A command to send to a device."""
    device_id: str
    location: str
    target: str
    action: str
    value: Any
    reason: str | None = None
    ttl: int = 30000
    correlation_id: str | None = None

    def __post_init__(self):
        if self.correlation_id is None:
            self.correlation_id = f"ai-{uuid.uuid4().hex[:8]}"

    def to_mqtt_payload(self) -> dict[str, Any]:
        """Convert to MQTT message envelope."""
        return {
            "v": 1,
            "ts": int(time.time() * 1000),
            "correlationId": self.correlation_id,
            "source": "ai-orchestrator",
            "deviceId": self.device_id,
            "location": self.location,
            "type": "command",
            "payload": {
                "target": self.target,
                "action": self.action,
                "value": self.value,
                "reason": self.reason,
                "ttl": self.ttl
            }
        }

    @property
    def topic(self) -> str:
        """Get the MQTT topic for this command."""
        return f"home/{self.location}/{self.device_id}/command"


@dataclass
class CommandAck:
    """Acknowledgment from a device."""
    correlation_id: str
    status: str  # executed, rejected, error, expired
    target: str
    actual_value: Any
    error: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandAck":
        """Parse an ack message from MQTT payload."""
        payload = data.get("payload", {})
        return cls(
            correlation_id=payload.get("correlationId", ""),
            status=payload.get("status", ""),
            target=payload.get("target", ""),
            actual_value=payload.get("actualValue"),
            error=payload.get("error")
        )
