"""Shared fixtures for Cortex tests."""

import os
import sqlite3
import tempfile
import time

import pytest

# Point config at test values before importing any src modules
os.environ.setdefault("SQLITE_PATH", ":memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("MQTT_HOST", "localhost")


@pytest.fixture
def sqlite_db():
    """Create a temporary SQLite database with the Cortex schema."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()

    from src.services.sqlite_client import SqliteClient

    client = SqliteClient(tmp.name, journal_mode="WAL")
    client.connect()
    yield client
    client.close()
    os.unlink(tmp.name)


@pytest.fixture
def mock_redis():
    """A lightweight mock Redis that stores readings in memory."""

    class MockRedis:
        def __init__(self):
            self._readings = []

        def store_reading(self, ts, temp, humidity, source_topic=None, device_id=""):
            from src.services.redis_client import RedisReading

            self._readings.append(
                RedisReading(
                    ts=ts,
                    temp=temp,
                    humidity=humidity,
                    source_topic=source_topic,
                    device_id=device_id,
                )
            )

        def get_readings_in_range(self, since_ms, until_ms, device_id=None):
            results = [
                r
                for r in self._readings
                if since_ms <= r.ts <= until_ms
                and (device_id is None or r.device_id == device_id)
            ]
            return results

        def get_all_readings(self):
            return list(self._readings)

        def delete_readings(self, *args, **kwargs):
            pass

    return MockRedis()


@pytest.fixture
def telemetry_factory():
    """Factory for creating TelemetryMessage instances."""

    def _make(device_id="esp32-test", location="room1", temp=22.0, humidity=55.0, ts=None):
        from src.models.telemetry import TelemetryMessage, Reading

        return TelemetryMessage(
            version=1,
            ts=ts or int(time.time() * 1000),
            device_id=device_id,
            location=location,
            readings=[
                Reading(id="temp1", value=temp, unit="C"),
                Reading(id="hum1", value=humidity, unit="%"),
            ],
        )

    return _make
