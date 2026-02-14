"""
MQTT client for the Cortex backend.

Handles all MQTT topics: telemetry, commands, acks, device birth/will.
Stores telemetry to Redis + SQLite, broadcasts via WebSocket.
Also provides the decision engine integration from the original orchestrator.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable

import paho.mqtt.client as mqtt

from ..config import MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD
from ..models.telemetry import TelemetryMessage
from ..models.command import Command, CommandAck

logger = logging.getLogger(__name__)


class MqttService:
    """MQTT client for the Cortex backend — handles all message types."""

    def __init__(
        self,
        # Decision engine callbacks (from original orchestrator)
        on_telemetry: Callable[[TelemetryMessage], None] | None = None,
        on_ack: Callable[[CommandAck], None] | None = None,
        on_device_birth: Callable[[str, str, dict], None] | None = None,
        on_device_offline: Callable[[str], None] | None = None,
        # Storage + broadcast services (new in Phase 0)
        sqlite=None,
        redis_client=None,
        ws_server=None,
        event_loop: asyncio.AbstractEventLoop | None = None,
    ):
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="cortex",
        )
        self._on_telemetry = on_telemetry
        self._on_ack = on_ack
        self._on_device_birth = on_device_birth
        self._on_device_offline = on_device_offline
        self._sqlite = sqlite
        self._redis = redis_client
        self._ws = ws_server
        self._event_loop = event_loop
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
        self.client.disconnect()

    def loop_start(self) -> None:
        self.client.loop_start()

    def loop_stop(self) -> None:
        self.client.loop_stop()

    def loop_forever(self) -> None:
        self.client.loop_forever()

    def publish_command(self, command: Command) -> str:
        """Publish a command to a device via the Command model. Returns correlation ID."""
        payload = json.dumps(command.to_mqtt_payload())
        self.client.publish(command.topic, payload, qos=1)
        self._pending_commands[command.correlation_id] = command
        logger.info(f"Published command {command.correlation_id} to {command.topic}")
        return command.correlation_id

    def publish_json(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish a raw JSON payload to a topic (used by intent executor)."""
        self.client.publish(topic, json.dumps(payload), qos=1)
        logger.debug(f"Published to {topic}")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── MQTT Callbacks ───────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            logger.info("Connected to MQTT broker")
            self._connected = True

            topics = [
                ("home/+/+/telemetry", 0),
                ("home/+/+/command", 0),
                ("home/+/+/ack", 0),
                ("home/_registry/+/birth", 0),
                ("home/_registry/+/will", 0),
            ]
            self.client.subscribe(topics)
            logger.info(f"Subscribed to: {[t[0] for t in topics]}")
        else:
            logger.error(f"Failed to connect: {reason_code}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self._connected = False
        logger.warning(f"Disconnected from MQTT broker: {reason_code}")

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            topic = msg.topic

            # Validate envelope format
            if not isinstance(payload, dict) or "type" not in payload or "payload" not in payload:
                logger.debug(f"Ignoring non-envelope message on {topic}")
                return

            msg_type = payload.get("type")
            device_id = payload.get("deviceId", "")
            location_val = payload.get("location", "")

            if msg_type == "telemetry":
                self._handle_telemetry(topic, payload)
            elif msg_type == "command":
                self._handle_command(payload)
            elif msg_type == "ack":
                self._handle_ack(device_id, payload.get("payload", {}))
            elif msg_type == "birth":
                self._handle_birth(device_id, location_val, payload.get("payload", {}))
            elif msg_type == "will":
                self._handle_will(device_id)
            else:
                logger.debug(f"Unknown message type: {msg_type} on {topic}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse MQTT message: {e}")
        except Exception as e:
            logger.error(f"Error handling MQTT message: {e}", exc_info=True)

    # ── Message Handlers ─────────────────────────────────────────────

    def _handle_telemetry(self, topic: str, envelope: dict[str, Any]) -> None:
        """Handle telemetry: store to Redis, broadcast via WebSocket, feed decision engine."""
        device_id = envelope.get("deviceId", "")
        inner = envelope.get("payload", {})

        if not isinstance(inner, dict):
            return

        readings = inner.get("readings", [])
        if not isinstance(readings, list):
            return

        # Extract temp and humidity (same logic as Node.js)
        temp = None
        humidity = None
        for reading in readings:
            if reading.get("id") == "temp1":
                temp = reading.get("value")
            if reading.get("id") == "hum1":
                humidity = reading.get("value")

        if temp is None or humidity is None:
            logger.debug(f"Incomplete telemetry from {device_id}: {readings}")
            return

        now = int(time.time() * 1000)
        latest_reading = {
            "temp": temp,
            "humidity": humidity,
            "updatedAt": now,
            "sourceTopic": topic,
            "deviceId": device_id,
        }

        # Store to Redis
        if self._redis:
            try:
                from .redis_client import RedisReading
                self._redis.store_reading(RedisReading(
                    ts=now, temp=temp, humidity=humidity,
                    source_topic=topic, device_id=device_id,
                ))
            except Exception as e:
                logger.error(f"Failed to store reading in Redis: {e}")

        # Broadcast via WebSocket
        if self._ws and self._event_loop:
            asyncio.run_coroutine_threadsafe(
                self._ws.broadcast_latest(latest_reading),
                self._event_loop,
            )

        # Feed decision engine (original orchestrator behavior)
        if self._on_telemetry:
            telemetry = TelemetryMessage.from_dict(envelope)
            self._on_telemetry(telemetry)

    def _handle_command(self, envelope: dict[str, Any]) -> None:
        """Handle command messages from any source — store in SQLite, broadcast."""
        device_id = envelope.get("deviceId", "")
        correlation_id = envelope.get("correlationId")
        source = envelope.get("source", "unknown")
        ts = envelope.get("ts", int(time.time() * 1000))
        inner = envelope.get("payload", {})

        if not correlation_id:
            logger.debug(f"Command missing correlationId from {source}")
            return

        if not isinstance(inner, dict):
            return

        target = inner.get("target")
        action = inner.get("action")

        if not target or not action:
            logger.debug(f"Command missing target or action from {source}")
            return

        # Skip if we already have this command
        if self._sqlite:
            existing = self._sqlite.get_command(correlation_id)
            if existing:
                logger.debug(f"Command {correlation_id} already exists, skipping")
                return

            if self._sqlite.has_pending_command_for_target(device_id, target):
                logger.debug(f"Skipping command {correlation_id} — pending for {device_id}/{target}")
                return

            logger.info(f"Received command {correlation_id} from {source}: {target} {action}")
            cmd = self._sqlite.insert_command(
                id=correlation_id, ts=ts, device_id=device_id,
                target=target, action=action, value=inner.get("value"),
                source=source, reason=inner.get("reason"),
            )

            if self._ws and self._event_loop:
                asyncio.run_coroutine_threadsafe(
                    self._ws.broadcast_command(self._sqlite.command_to_dict(cmd)),
                    self._event_loop,
                )

    def _handle_ack(self, device_id: str, payload: dict[str, Any]) -> None:
        """Handle command acknowledgment — update status, sync actuator state, broadcast."""
        correlation_id = payload.get("correlationId")
        if not correlation_id:
            logger.debug(f"Ack missing correlationId from {device_id}")
            return

        logger.info(f"Ack received for {correlation_id}: {payload.get('status')}")

        # Map device status to our status
        raw_status = payload.get("status", "")
        status = "failed" if raw_status in ("rejected", "error", "expired") else "acked"

        now = int(time.time() * 1000)

        if self._sqlite:
            self._sqlite.update_command_ack(correlation_id, status, now, payload)

            # Sync actuator state
            target = payload.get("target")
            actual_value = payload.get("actualValue")
            if status == "acked" and target and actual_value is not None:
                self._sqlite.update_actuator_state(device_id, target, bool(actual_value))

            # Insert + broadcast event
            event_payload = {**payload, "status": status}
            event = self._sqlite.insert_event(
                ts=now, device_id=device_id,
                event_type="command_ack", payload=event_payload, source="device",
            )

            if self._ws and self._event_loop:
                asyncio.run_coroutine_threadsafe(
                    self._ws.broadcast_event(self._sqlite.event_to_dict(event)),
                    self._event_loop,
                )

                if status == "acked" and target and actual_value is not None:
                    asyncio.run_coroutine_threadsafe(
                        self._ws.broadcast_devices(self._sqlite),
                        self._event_loop,
                    )

        # Remove from pending
        if correlation_id in self._pending_commands:
            del self._pending_commands[correlation_id]

        # Decision engine callback
        if self._on_ack:
            ack = CommandAck.from_dict({"payload": payload})
            self._on_ack(ack)

    def _handle_birth(self, device_id: str, location: str, payload: Any) -> None:
        """Handle device birth — upsert in SQLite, broadcast."""
        logger.info(f"Device birth: {device_id} at {location}")

        p = payload if isinstance(payload, dict) else {}

        if self._sqlite:
            self._sqlite.upsert_device(
                id=device_id,
                location=location,
                name=p.get("name"),
                platform=p.get("platform"),
                firmware=p.get("firmware"),
                capabilities=p.get("capabilities", {"sensors": [], "actuators": []}),
                telemetry_interval_ms=p.get("telemetryIntervalMs"),
                online=True,
                last_seen=int(time.time() * 1000),
            )

            event = self._sqlite.insert_event(
                ts=int(time.time() * 1000), device_id=device_id,
                event_type="device_birth", payload=payload, source="device",
            )

            if self._ws and self._event_loop:
                asyncio.run_coroutine_threadsafe(
                    self._ws.broadcast_event(self._sqlite.event_to_dict(event)),
                    self._event_loop,
                )
                asyncio.run_coroutine_threadsafe(
                    self._ws.broadcast_devices(self._sqlite),
                    self._event_loop,
                )

        # Decision engine callback
        if self._on_device_birth:
            self._on_device_birth(device_id, location, p)

    def _handle_will(self, device_id: str) -> None:
        """Handle device offline — mark offline, broadcast."""
        logger.info(f"Device offline: {device_id}")

        if self._sqlite:
            self._sqlite.set_device_offline(device_id)

            event = self._sqlite.insert_event(
                ts=int(time.time() * 1000), device_id=device_id,
                event_type="device_offline", payload={"status": "offline"}, source="device",
            )

            if self._ws and self._event_loop:
                asyncio.run_coroutine_threadsafe(
                    self._ws.broadcast_event(self._sqlite.event_to_dict(event)),
                    self._event_loop,
                )
                asyncio.run_coroutine_threadsafe(
                    self._ws.broadcast_devices(self._sqlite),
                    self._event_loop,
                )

        if self._on_device_offline:
            self._on_device_offline(device_id)
