"""Unit tests for the generic sensor_values table and CRUD methods."""

import time

from src.services.sqlite_client import SensorValue


class TestSensorValues:
    def test_insert_and_query(self, sqlite_db):
        """Basic insert and query round-trip."""
        now = int(time.time() * 1000)
        values = [
            SensorValue(ts=now, device_id="esp32-1", sensor_id="temp1", value=22.5, source_topic="mqtt"),
            SensorValue(ts=now, device_id="esp32-1", sensor_id="hum1", value=55.0, source_topic="mqtt"),
        ]
        sqlite_db.insert_sensor_values(values)

        result = sqlite_db.query_sensor_values(now - 1000, now + 1000, device_id="esp32-1")
        assert len(result) == 2
        sensor_ids = {r.sensor_id for r in result}
        assert sensor_ids == {"temp1", "hum1"}

    def test_query_by_sensor_id(self, sqlite_db):
        """Filter by specific sensor_id."""
        now = int(time.time() * 1000)
        sqlite_db.insert_sensor_values([
            SensorValue(ts=now, device_id="esp32-1", sensor_id="temp1", value=22.5),
            SensorValue(ts=now, device_id="esp32-1", sensor_id="hum1", value=55.0),
            SensorValue(ts=now, device_id="esp32-1", sensor_id="soil1", value=42.0),
        ])

        result = sqlite_db.query_sensor_values(now - 1000, now + 1000, sensor_id="soil1")
        assert len(result) == 1
        assert result[0].sensor_id == "soil1"
        assert result[0].value == 42.0

    def test_query_by_device_id(self, sqlite_db):
        """Filter by device_id."""
        now = int(time.time() * 1000)
        sqlite_db.insert_sensor_values([
            SensorValue(ts=now, device_id="esp32-1", sensor_id="temp1", value=22.5),
            SensorValue(ts=now, device_id="esp32-2", sensor_id="temp1", value=24.0),
        ])

        result = sqlite_db.query_sensor_values(now - 1000, now + 1000, device_id="esp32-1")
        assert len(result) == 1
        assert result[0].value == 22.5

    def test_query_time_range(self, sqlite_db):
        """Only returns readings within the time range."""
        now = int(time.time() * 1000)
        sqlite_db.insert_sensor_values([
            SensorValue(ts=now - 120_000, device_id="esp32-1", sensor_id="temp1", value=20.0),
            SensorValue(ts=now - 60_000, device_id="esp32-1", sensor_id="temp1", value=21.0),
            SensorValue(ts=now, device_id="esp32-1", sensor_id="temp1", value=22.0),
        ])

        result = sqlite_db.query_sensor_values(now - 90_000, now + 1000)
        assert len(result) == 2  # Excludes the oldest

    def test_insert_empty_list(self, sqlite_db):
        """Inserting empty list is a no-op."""
        sqlite_db.insert_sensor_values([])
        result = sqlite_db.query_sensor_values(0, int(time.time() * 1000) + 1000)
        assert len(result) == 0

    def test_bucketed_query(self, sqlite_db):
        """Bucketed query averages values correctly."""
        # Use a base aligned to bucket boundary so all 3 readings fall in one bucket
        bucket_ms = 60_000
        base = bucket_ms * 100  # 6_000_000 — exactly on a 60s boundary

        sqlite_db.insert_sensor_values([
            SensorValue(ts=base, device_id="esp32-1", sensor_id="temp1", value=20.0),
            SensorValue(ts=base + 10_000, device_id="esp32-1", sensor_id="temp1", value=24.0),
            SensorValue(ts=base + 20_000, device_id="esp32-1", sensor_id="temp1", value=22.0),
        ])

        result = sqlite_db.query_sensor_values_bucketed(
            since_ms=base - 1000,
            until_ms=base + 60_000,
            bucket_ms=bucket_ms,
        )
        assert len(result) == 1
        assert result[0]["sensor_id"] == "temp1"
        assert abs(result[0]["value"] - 22.0) < 0.01  # avg of 20, 24, 22
        assert result[0]["count"] == 3

    def test_bucketed_query_multiple_sensors(self, sqlite_db):
        """Bucketed query groups by sensor_id."""
        base = 1000000
        bucket_ms = 60_000

        sqlite_db.insert_sensor_values([
            SensorValue(ts=base, device_id="esp32-1", sensor_id="temp1", value=20.0),
            SensorValue(ts=base, device_id="esp32-1", sensor_id="hum1", value=50.0),
            SensorValue(ts=base + 10_000, device_id="esp32-1", sensor_id="temp1", value=22.0),
            SensorValue(ts=base + 10_000, device_id="esp32-1", sensor_id="hum1", value=54.0),
        ])

        result = sqlite_db.query_sensor_values_bucketed(
            since_ms=base - 1000,
            until_ms=base + 60_000,
            bucket_ms=bucket_ms,
        )
        assert len(result) == 2
        by_sensor = {r["sensor_id"]: r for r in result}
        assert abs(by_sensor["temp1"]["value"] - 21.0) < 0.01
        assert abs(by_sensor["hum1"]["value"] - 52.0) < 0.01

    def test_diverse_sensor_types(self, sqlite_db):
        """Works with non-temp/humidity sensor types."""
        now = int(time.time() * 1000)
        sqlite_db.insert_sensor_values([
            SensorValue(ts=now, device_id="grow-1", sensor_id="soil1", value=42.0),
            SensorValue(ts=now, device_id="grow-1", sensor_id="soil2", value=38.5),
            SensorValue(ts=now, device_id="grow-1", sensor_id="light1", value=800.0),
            SensorValue(ts=now, device_id="garage-1", sensor_id="contact1", value=1.0),
        ])

        result = sqlite_db.query_sensor_values(now - 1000, now + 1000, device_id="grow-1")
        assert len(result) == 3
        sensor_ids = {r.sensor_id for r in result}
        assert sensor_ids == {"soil1", "soil2", "light1"}

    def test_migration_from_sensor_readings(self, sqlite_db):
        """Old sensor_readings data is migrated to sensor_values on connect."""
        # Insert old-format data
        db = sqlite_db._get_db()
        db.execute(
            "INSERT INTO sensor_readings (ts, temp, humidity, source_topic, device_id) VALUES (?, ?, ?, ?, ?)",
            (1000, 22.5, 55.0, "mqtt", "esp32-1"),
        )
        db.commit()

        # Create a fresh client to trigger migration
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()

        from src.services.sqlite_client import SqliteClient
        fresh = SqliteClient(tmp.name, journal_mode="WAL")
        fresh.connect()

        # Insert old data into the fresh DB's sensor_readings
        fdb = fresh._get_db()
        fdb.execute(
            "INSERT INTO sensor_readings (ts, temp, humidity, source_topic, device_id) VALUES (?, ?, ?, ?, ?)",
            (1000, 22.5, 55.0, "mqtt", "esp32-1"),
        )
        fdb.commit()

        # Re-run migrations (close and re-connect triggers migration)
        fresh.close()
        fresh2 = SqliteClient(tmp.name, journal_mode="WAL")
        fresh2.connect()

        result = fresh2.query_sensor_values(0, 2000)
        assert len(result) == 2  # temp1 + hum1
        by_sensor = {r.sensor_id: r.value for r in result}
        assert by_sensor["temp1"] == 22.5
        assert by_sensor["hum1"] == 55.0

        fresh2.close()
        os.unlink(tmp.name)

    def test_limit_respected(self, sqlite_db):
        """Query respects the limit parameter."""
        now = int(time.time() * 1000)
        sqlite_db.insert_sensor_values([
            SensorValue(ts=now + i, device_id="esp32-1", sensor_id="temp1", value=20.0 + i)
            for i in range(50)
        ])

        result = sqlite_db.query_sensor_values(now - 1000, now + 100_000, limit=10)
        assert len(result) == 10
