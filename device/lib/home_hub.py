# device/lib/home_hub.py
"""
HomeHubClient - Shared library for ESP32 devices implementing the messaging contract.

Handles device registration, telemetry publishing, command handling, and acknowledgments.
"""

import json
import time


class HomeHubClient:
    VERSION = 1

    def __init__(self, device_id: str, location: str, mqtt_client, platform: str = "esp32"):
        """
        Initialize the HomeHub client.

        Args:
            device_id: Unique identifier for this device (e.g., "esp32-001")
            location: Physical location (e.g., "garage", "kitchen")
            mqtt_client: MqttService instance with publish/subscribe capabilities
            platform: Hardware platform identifier
        """
        self.device_id = device_id
        self.location = location
        self.platform = platform
        self.mqtt = mqtt_client
        self.capabilities = {"sensors": [], "actuators": []}
        self._command_handler = None
        self._firmware_version = "1.0.0"

    def set_firmware_version(self, version: str):
        """Set the firmware version reported in birth messages."""
        self._firmware_version = version

    def register_sensor(self, id: str, type: str, unit: str = None, values: list = None):
        """
        Register a sensor capability.

        Args:
            id: Sensor identifier (e.g., "temp1")
            type: Sensor type (e.g., "temperature", "humidity", "contact", "motion")
            unit: Measurement unit (e.g., "celsius", "percent")
            values: For discrete sensors, list of possible values (e.g., ["open", "closed"])
        """
        sensor = {"id": id, "type": type}
        if unit:
            sensor["unit"] = unit
        if values:
            sensor["values"] = values
        self.capabilities["sensors"].append(sensor)

    def register_actuator(self, id: str, type: str, name: str, **kwargs):
        """
        Register an actuator capability.

        Args:
            id: Actuator identifier (e.g., "relay1")
            type: Actuator type (e.g., "switch", "dimmer", "cover")
            name: Human-readable name (e.g., "Garage Light")
            **kwargs: Additional properties (e.g., min=0, max=100 for dimmers)
        """
        actuator = {"id": id, "type": type, "name": name}
        actuator.update(kwargs)
        self.capabilities["actuators"].append(actuator)

    def publish_birth(self, telemetry_interval_ms: int = 5000):
        """
        Announce device presence and capabilities to the system.

        Should be called after MQTT connect.

        Args:
            telemetry_interval_ms: How often this device sends telemetry
        """
        payload = {
            "name": f"{self.location} {self.device_id}",
            "platform": self.platform,
            "firmware": self._firmware_version,
            "capabilities": self.capabilities,
            "telemetryIntervalMs": telemetry_interval_ms
        }
        self._publish("birth", payload, topic=f"home/_registry/{self.device_id}/birth")

    def set_last_will(self):
        """
        Configure MQTT last-will message for offline detection.

        MUST be called BEFORE mqtt.connect().
        """
        will_topic = f"home/_registry/{self.device_id}/will"
        will_msg = json.dumps({
            "v": self.VERSION,
            "deviceId": self.device_id,
            "type": "will",
            "payload": {"status": "offline"}
        })
        self.mqtt.set_last_will(will_topic, will_msg)

    def publish_telemetry(self, readings: list):
        """
        Publish sensor readings.

        Args:
            readings: List of reading dicts, e.g., [{"id": "temp1", "value": 23.5}, ...]
        """
        self._publish("telemetry", {"readings": readings})

    def publish_status(self, uptime_ms: int, free_heap: int = None, rssi: int = None):
        """
        Publish device health/heartbeat status.

        Args:
            uptime_ms: Milliseconds since device boot
            free_heap: Available heap memory in bytes
            rssi: WiFi signal strength in dBm
        """
        payload = {"uptime": uptime_ms}
        if free_heap is not None:
            payload["freeHeap"] = free_heap
        if rssi is not None:
            payload["rssi"] = rssi
        self._publish("status", payload)

    def publish_ack(self, correlation_id: str, status: str, target: str, actual_value, error: str = None):
        """
        Acknowledge a received command.

        Args:
            correlation_id: The correlationId from the command
            status: One of "executed", "rejected", "error", "expired", "queued"
            target: The actuator that was targeted
            actual_value: The resulting value after the command
            error: Error message if status is "error" or "rejected"
        """
        payload = {
            "correlationId": correlation_id,
            "status": status,
            "target": target,
            "actualValue": actual_value
        }
        if error:
            payload["error"] = error
        self._publish("ack", payload)

    def on_command(self, handler):
        """
        Set command handler and subscribe to command topic.

        The handler will be called with:
            handler(correlation_id, target, action, value, ttl)

        Args:
            handler: Callback function to handle incoming commands
        """
        self._command_handler = handler
        topic = f"home/{self.location}/{self.device_id}/command"
        self.mqtt.subscribe(topic)
        self.mqtt.set_callback(self._handle_message)

    def _handle_message(self, topic: bytes, msg: bytes):
        """Parse incoming command and invoke handler."""
        try:
            data = json.loads(msg.decode())
            if data.get("type") == "command" and self._command_handler:
                payload = data.get("payload", {})
                self._command_handler(
                    data.get("correlationId"),
                    payload.get("target"),
                    payload.get("action"),
                    payload.get("value"),
                    payload.get("ttl")
                )
        except Exception as e:
            print(f"[HomeHub] Command parse error: {e}")

    def check_messages(self):
        """
        Non-blocking check for incoming commands.

        Call this in the main loop to process incoming MQTT messages.
        """
        self.mqtt.check_msg()

    def _publish(self, msg_type: str, payload: dict, topic: str = None):
        """Wrap payload in envelope and publish to MQTT."""
        envelope = {
            "v": self.VERSION,
            "ts": int(time.time() * 1000),
            "deviceId": self.device_id,
            "location": self.location,
            "type": msg_type,
            "payload": payload
        }
        if topic is None:
            topic = f"home/{self.location}/{self.device_id}/{msg_type}"
        self.mqtt.publish(topic, json.dumps(envelope))
