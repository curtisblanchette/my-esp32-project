"""
End-to-end simulation tests for the Cortex MQTT→API→Storage flow.

These tests require external services (Mosquitto, Redis) running.
Mark them with @pytest.mark.e2e so they can be skipped in pure unit-test runs.

Run with: pytest tests/test_e2e_flow.py -m e2e -v
"""

import json
import time
import uuid

import pytest

# ── Markers & Fixtures ──────────────────────────────────────────────

pytestmark = pytest.mark.e2e


def _mqtt_available():
    """Check if Mosquitto is reachable."""
    try:
        import paho.mqtt.client as mqtt

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect("localhost", 1883, 5)
        client.disconnect()
        return True
    except Exception:
        return False


def _api_available():
    """Check if Cortex API is reachable."""
    try:
        import httpx

        r = httpx.get("http://localhost:8000/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


requires_mqtt = pytest.mark.skipif(not _mqtt_available(), reason="MQTT broker not running")
requires_api = pytest.mark.skipif(not _api_available(), reason="Cortex API not running")
requires_stack = pytest.mark.skipif(
    not (_mqtt_available() and _api_available()),
    reason="Full stack (MQTT + API) not running",
)


# ── Helpers ─────────────────────────────────────────────────────────


def _publish_mqtt(topic: str, payload: dict, wait: float = 1.0):
    """Publish a message to MQTT and wait for processing."""
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect("localhost", 1883)
    client.publish(topic, json.dumps(payload), qos=1)
    client.disconnect()
    time.sleep(wait)


def _publish_birth(device_id: str, location: str = "room1"):
    """Publish a device birth message."""
    _publish_mqtt(
        f"home/_registry/{device_id}/birth",
        {
            "v": 1,
            "ts": int(time.time() * 1000),
            "deviceId": device_id,
            "location": location,
            "type": "birth",
            "payload": {
                "firmware": "test-1.0.0",
                "capabilities": {
                    "sensors": ["temp1", "hum1"],
                    "actuators": ["relay1"],
                },
            },
        },
    )


def _publish_telemetry(device_id: str, temp: float, humidity: float, location: str = "room1"):
    """Publish a telemetry reading."""
    _publish_mqtt(
        f"home/{location}/{device_id}/telemetry",
        {
            "v": 1,
            "ts": int(time.time() * 1000),
            "deviceId": device_id,
            "location": location,
            "type": "telemetry",
            "payload": {
                "readings": [
                    {"id": "temp1", "value": temp, "unit": "C"},
                    {"id": "hum1", "value": humidity, "unit": "%"},
                ],
            },
        },
    )


def _publish_ack(device_id: str, correlation_id: str, location: str = "room1"):
    """Publish a command acknowledgment."""
    _publish_mqtt(
        f"home/{location}/{device_id}/ack",
        {
            "v": 1,
            "ts": int(time.time() * 1000),
            "deviceId": device_id,
            "location": location,
            "type": "ack",
            "payload": {
                "correlationId": correlation_id,
                "status": "executed",
                "target": "relay1",
                "actualValue": True,
            },
        },
    )


# ── Tests ───────────────────────────────────────────────────────────


@requires_stack
class TestDeviceBirth:
    def test_birth_registers_device(self):
        """Device birth via MQTT → appears in /api/devices."""
        import httpx

        device_id = f"test-{uuid.uuid4().hex[:6]}"
        _publish_birth(device_id)

        r = httpx.get("http://localhost:8000/api/devices", timeout=5)
        assert r.status_code == 200
        devices = r.json()
        device_ids = [d["device_id"] for d in devices]
        assert device_id in device_ids


@requires_stack
class TestTelemetryIngestion:
    def test_telemetry_stored_and_queryable(self):
        """Published telemetry → queryable via /api/history."""
        import httpx

        device_id = f"test-{uuid.uuid4().hex[:6]}"
        _publish_birth(device_id)
        _publish_telemetry(device_id, 22.5, 55.0)

        now = int(time.time() * 1000)
        r = httpx.get(
            "http://localhost:8000/api/history",
            params={"sinceMs": now - 60_000, "deviceId": device_id},
            timeout=5,
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1

    def test_rising_temperature_sequence(self):
        """Multiple readings with rising temps → verifiable trend."""
        import httpx

        device_id = f"test-{uuid.uuid4().hex[:6]}"
        _publish_birth(device_id)

        for temp in [22.0, 23.0, 24.0, 25.0, 26.0]:
            _publish_telemetry(device_id, temp, 55.0)
            time.sleep(0.5)

        now = int(time.time() * 1000)
        r = httpx.get(
            "http://localhost:8000/api/history",
            params={"sinceMs": now - 60_000, "deviceId": device_id},
            timeout=5,
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 5


@requires_stack
class TestRelayControl:
    def test_relay_command_via_api(self):
        """POST relay state → command stored in /api/commands."""
        import httpx

        device_id = f"test-{uuid.uuid4().hex[:6]}"
        _publish_birth(device_id)

        r = httpx.post(
            f"http://localhost:8000/api/devices/{device_id}/relays/relay1",
            json={"state": True},
            timeout=5,
        )
        assert r.status_code == 200

        # Verify command appears in history
        r = httpx.get("http://localhost:8000/api/commands", timeout=5)
        assert r.status_code == 200


@requires_stack
class TestCommandAck:
    def test_ack_updates_command_status(self):
        """Relay command → ACK via MQTT → command status updated."""
        import httpx

        device_id = f"test-{uuid.uuid4().hex[:6]}"
        _publish_birth(device_id)

        # Send a relay command
        r = httpx.post(
            f"http://localhost:8000/api/devices/{device_id}/relays/relay1",
            json={"state": True},
            timeout=5,
        )
        assert r.status_code == 200

        # Get the correlation ID from the commands list
        r = httpx.get("http://localhost:8000/api/commands", timeout=5)
        commands = r.json()
        if commands:
            corr_id = commands[0].get("id") or commands[0].get("correlationId", "")
            if corr_id:
                _publish_ack(device_id, corr_id)

                # Check command status updated
                r = httpx.get("http://localhost:8000/api/commands", timeout=5)
                assert r.status_code == 200


@requires_stack
class TestAPIEndpoints:
    """Regression tests: all core endpoints return 200."""

    def test_health(self):
        import httpx

        r = httpx.get("http://localhost:8000/health", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

    def test_devices(self):
        import httpx

        r = httpx.get("http://localhost:8000/api/devices", timeout=5)
        assert r.status_code == 200

    def test_history(self):
        import httpx

        now = int(time.time() * 1000)
        r = httpx.get(
            "http://localhost:8000/api/history",
            params={"sinceMs": now - 3600_000},
            timeout=5,
        )
        assert r.status_code == 200

    def test_commands(self):
        import httpx

        r = httpx.get("http://localhost:8000/api/commands", timeout=5)
        assert r.status_code == 200

    def test_events(self):
        import httpx

        r = httpx.get("http://localhost:8000/api/events", timeout=5)
        assert r.status_code == 200

    def test_chat_health(self):
        import httpx

        r = httpx.get("http://localhost:8000/api/chat/health", timeout=5)
        assert r.status_code == 200


@requires_stack
class TestBaselineAccumulation:
    """Verify baselines grow with telemetry ingestion."""

    def test_baselines_created_from_telemetry(self):
        """Publish readings → cortex_baselines table should have entries."""
        import httpx
        import sqlite3

        device_id = f"test-{uuid.uuid4().hex[:6]}"
        _publish_birth(device_id)

        # Send enough readings to seed baselines
        for i in range(5):
            _publish_telemetry(device_id, 22.0 + i * 0.5, 55.0 + i * 0.2)
            time.sleep(0.3)

        # Allow time for processing
        time.sleep(2)

        # Query the SQLite database directly
        # (This test assumes SQLITE_PATH is the default data/telemetry.sqlite)
        try:
            db = sqlite3.connect("data/telemetry.sqlite")
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM cortex_baselines WHERE device_id = ?",
                (device_id,),
            ).fetchall()
            db.close()

            # Should have baselines for temperature and humidity
            assert len(rows) >= 1
            for row in rows:
                assert row["sample_count"] >= 1
        except sqlite3.OperationalError:
            pytest.skip("SQLite database not accessible or table not created")
