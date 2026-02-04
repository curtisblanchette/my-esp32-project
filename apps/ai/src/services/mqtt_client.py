import json
import logging
import time
from typing import Callable, Any

import paho.mqtt.client as mqtt

from ..config import MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD
from ..models.telemetry import TelemetryMessage
from ..models.command import Command, CommandAck

logger = logging.getLogger(__name__)


class MqttService:
    """MQTT client for the AI orchestrator."""

    def __init__(
        self,
        on_telemetry: Callable[[TelemetryMessage], None] | None = None,
        on_ack: Callable[[CommandAck], None] | None = None,
        on_device_birth: Callable[[str, str, dict], None] | None = None,
        on_device_offline: Callable[[str], None] | None = None,
    ):
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="ai-orchestrator"
        )
        self._on_telemetry = on_telemetry
        self._on_ack = on_ack
        self._on_device_birth = on_device_birth
        self._on_device_offline = on_device_offline
        self._connected = False
        self._pending_commands: dict[str, Command] = {}

        # Set up callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        # Set credentials if provided
        if MQTT_USERNAME and MQTT_PASSWORD:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    def connect(self) -> None:
        """Connect to the MQTT broker."""
        logger.info(f"Connecting to MQTT broker at {MQTT_HOST}:{MQTT_PORT}")
        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

    def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        self.client.disconnect()

    def loop_start(self) -> None:
        """Start the MQTT client loop in a background thread."""
        self.client.loop_start()

    def loop_stop(self) -> None:
        """Stop the MQTT client loop."""
        self.client.loop_stop()

    def loop_forever(self) -> None:
        """Run the MQTT client loop (blocking)."""
        self.client.loop_forever()

    def publish_command(self, command: Command) -> str:
        """Publish a command to a device. Returns correlation ID."""
        payload = json.dumps(command.to_mqtt_payload())
        self.client.publish(command.topic, payload, qos=1)
        self._pending_commands[command.correlation_id] = command
        logger.info(f"Published command {command.correlation_id} to {command.topic}")
        return command.correlation_id

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        """Handle MQTT connection."""
        if reason_code == 0:
            logger.info("Connected to MQTT broker")
            self._connected = True

            # Subscribe to topics (including legacy format for backward compatibility)
            topics = [
                ("home/+/+/telemetry", 0),
                ("home/+/+/ack", 0),
                ("home/_registry/+/birth", 0),
                ("home/_registry/+/will", 0),
                ("/device/+/telemetry", 0),  # Legacy topic format
            ]
            self.client.subscribe(topics)
            logger.info(f"Subscribed to: {[t[0] for t in topics]}")
        else:
            logger.error(f"Failed to connect: {reason_code}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        """Handle MQTT disconnection."""
        self._connected = False
        logger.warning(f"Disconnected from MQTT broker: {reason_code}")

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        """Handle incoming MQTT messages."""
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            topic = msg.topic

            # Check for legacy format: /device/{device_id}/telemetry
            if topic.startswith("/device/") and topic.endswith("/telemetry"):
                self._handle_legacy_telemetry(topic, payload)
                return

            # Parse message type from envelope
            msg_type = payload.get("type")

            if msg_type == "telemetry":
                self._handle_telemetry(payload)
            elif msg_type == "ack":
                self._handle_ack(payload)
            elif msg_type == "birth":
                self._handle_birth(payload)
            elif msg_type == "will":
                self._handle_will(payload)
            else:
                logger.debug(f"Unknown message type: {msg_type} on {topic}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse MQTT message: {e}")
        except Exception as e:
            logger.error(f"Error handling MQTT message: {e}")

    def _handle_telemetry(self, payload: dict[str, Any]) -> None:
        """Handle telemetry message."""
        if self._on_telemetry:
            telemetry = TelemetryMessage.from_dict(payload)
            logger.debug(f"Telemetry from {telemetry.device_id}: {telemetry.readings}")
            self._on_telemetry(telemetry)

    def _handle_legacy_telemetry(self, topic: str, payload: dict[str, Any]) -> None:
        """Handle legacy format telemetry: /device/{device_id}/telemetry with {tempC, humidity}."""
        if not self._on_telemetry:
            return

        # Extract device_id from topic: /device/{device_id}/telemetry
        parts = topic.split("/")
        device_id = parts[2] if len(parts) >= 3 else "unknown"

        # Convert legacy format to new format
        temp = payload.get("tempC") or payload.get("temp")
        humidity = payload.get("humidity")

        if temp is None or humidity is None:
            logger.debug(f"Legacy telemetry missing temp or humidity: {payload}")
            return

        # Create envelope-style payload for TelemetryMessage
        converted = {
            "v": 1,
            "ts": payload.get("ts", int(time.time() * 1000)),
            "deviceId": device_id,
            "location": "unknown",  # Legacy format doesn't include location
            "type": "telemetry",
            "payload": {
                "readings": [
                    {"id": "temp1", "value": temp},
                    {"id": "hum1", "value": humidity},
                ]
            }
        }

        telemetry = TelemetryMessage.from_dict(converted)
        logger.debug(f"Legacy telemetry from {device_id}: temp={temp}, humidity={humidity}")
        self._on_telemetry(telemetry)

    def _handle_ack(self, payload: dict[str, Any]) -> None:
        """Handle command acknowledgment."""
        ack = CommandAck.from_dict(payload)
        logger.info(f"Ack received: {ack.correlation_id} -> {ack.status}")

        # Remove from pending commands
        if ack.correlation_id in self._pending_commands:
            del self._pending_commands[ack.correlation_id]

        if self._on_ack:
            self._on_ack(ack)

    def _handle_birth(self, payload: dict[str, Any]) -> None:
        """Handle device birth message."""
        device_id = payload.get("deviceId", "")
        location = payload.get("location", "")
        capabilities = payload.get("payload", {})
        logger.info(f"Device birth: {device_id} at {location}")

        if self._on_device_birth:
            self._on_device_birth(device_id, location, capabilities)

    def _handle_will(self, payload: dict[str, Any]) -> None:
        """Handle device offline (will) message."""
        device_id = payload.get("deviceId", "")
        logger.info(f"Device offline: {device_id}")

        if self._on_device_offline:
            self._on_device_offline(device_id)

    @property
    def is_connected(self) -> bool:
        """Check if connected to MQTT broker."""
        return self._connected
