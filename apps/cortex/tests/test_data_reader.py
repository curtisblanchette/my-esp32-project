"""Unit tests for data_reader.py — unified Redis+SQLite merge layer."""

import time

from src.services.sqlite_client import SensorValue


def _insert_sv(db, ts, device_id="esp32-1", readings=None):
    """Insert sensor values into the new generic sensor_values table."""
    if readings is None:
        readings = {"temp1": 22.0, "hum1": 55.0}
    values = [
        SensorValue(ts=ts, device_id=device_id, sensor_id=sid, value=val, source_topic="mqtt")
        for sid, val in readings.items()
    ]
    db.insert_sensor_values(values)


class TestDataReader:
    def test_merge_redis_and_sqlite(self, sqlite_db, mock_redis):
        from src.services.data_reader import DataReader

        now = int(time.time() * 1000)

        # Insert into SQLite (older data)
        _insert_sv(sqlite_db, now - 120_000, readings={"temp1": 22.0, "hum1": 55.0})
        _insert_sv(sqlite_db, now - 60_000, readings={"temp1": 22.5, "hum1": 54.0})

        # Insert into mock Redis (newer data)
        mock_redis.store_reading(now - 30_000, 23.0, 53.0, "mqtt", "esp32-1")
        mock_redis.store_reading(now, 23.5, 52.0, "mqtt", "esp32-1")

        reader = DataReader(mock_redis, sqlite_db)
        readings = reader.get_readings(now - 200_000, now, device_id="esp32-1")

        assert len(readings) == 4
        # Should be sorted by timestamp
        for i in range(1, len(readings)):
            assert readings[i].ts >= readings[i - 1].ts

    def test_deduplication(self, sqlite_db, mock_redis):
        from src.services.data_reader import DataReader

        now = int(time.time() * 1000)
        ts = now - 60_000

        # Same timestamp in both sources
        _insert_sv(sqlite_db, ts, readings={"temp1": 22.0, "hum1": 55.0})
        mock_redis.store_reading(ts, 22.0, 55.0, "mqtt", "esp32-1")

        reader = DataReader(mock_redis, sqlite_db)
        readings = reader.get_readings(ts - 1000, now, device_id="esp32-1")

        assert len(readings) == 1

    def test_get_recent_readings(self, sqlite_db, mock_redis):
        from src.services.data_reader import DataReader

        now = int(time.time() * 1000)
        # Within 30-minute window
        mock_redis.store_reading(now - 5 * 60_000, 22.0, 55.0, "mqtt", "esp32-1")
        mock_redis.store_reading(now - 2 * 60_000, 22.5, 54.0, "mqtt", "esp32-1")
        # Outside 30-minute window
        mock_redis.store_reading(now - 45 * 60_000, 21.0, 56.0, "mqtt", "esp32-1")

        reader = DataReader(mock_redis, sqlite_db)
        readings = reader.get_recent_readings(window_minutes=30, device_id="esp32-1")

        assert len(readings) == 2

    def test_extract_metric_by_sensor_id(self, sqlite_db, mock_redis):
        from src.services.data_reader import DataReader, MergedReading

        reader = DataReader(mock_redis, sqlite_db)
        readings = [
            MergedReading(ts=1000, readings={"temp1": 22.0, "hum1": 55.0}, device_id="esp32-1"),
            MergedReading(ts=2000, readings={"temp1": 23.0, "hum1": 54.0}, device_id="esp32-1"),
        ]

        values, timestamps = reader.extract_metric(readings, "temp1")
        assert values == [22.0, 23.0]
        assert timestamps == [1000, 2000]

    def test_extract_metric_humidity_sensor(self, sqlite_db, mock_redis):
        from src.services.data_reader import DataReader, MergedReading

        reader = DataReader(mock_redis, sqlite_db)
        readings = [
            MergedReading(ts=1000, readings={"temp1": 22.0, "hum1": 55.0}, device_id="esp32-1"),
            MergedReading(ts=2000, readings={"temp1": 23.0, "hum1": 54.0}, device_id="esp32-1"),
        ]

        values, timestamps = reader.extract_metric(readings, "hum1")
        assert values == [55.0, 54.0]
        assert timestamps == [1000, 2000]

    def test_extract_metric_skips_missing(self, sqlite_db, mock_redis):
        """Readings that don't have the requested sensor_id are skipped."""
        from src.services.data_reader import DataReader, MergedReading

        reader = DataReader(mock_redis, sqlite_db)
        readings = [
            MergedReading(ts=1000, readings={"temp1": 22.0}, device_id="esp32-1"),
            MergedReading(ts=2000, readings={"hum1": 54.0}, device_id="esp32-1"),
            MergedReading(ts=3000, readings={"temp1": 24.0, "hum1": 53.0}, device_id="esp32-1"),
        ]

        values, timestamps = reader.extract_metric(readings, "temp1")
        assert values == [22.0, 24.0]
        assert timestamps == [1000, 3000]

    def test_limit_respected(self, sqlite_db, mock_redis):
        from src.services.data_reader import DataReader

        now = int(time.time() * 1000)
        for i in range(100):
            mock_redis.store_reading(now - i * 1000, 22.0 + i * 0.1, 55.0, "mqtt", "esp32-1")

        reader = DataReader(mock_redis, sqlite_db)
        readings = reader.get_readings(now - 200_000, now, device_id="esp32-1", limit=10)

        assert len(readings) == 10

    def test_device_filter(self, sqlite_db, mock_redis):
        from src.services.data_reader import DataReader

        now = int(time.time() * 1000)
        mock_redis.store_reading(now - 1000, 22.0, 55.0, "mqtt", "esp32-1")
        mock_redis.store_reading(now - 500, 23.0, 54.0, "mqtt", "esp32-2")

        reader = DataReader(mock_redis, sqlite_db)
        readings = reader.get_readings(now - 5000, now, device_id="esp32-1")
        assert len(readings) == 1
        assert readings[0].device_id == "esp32-1"

    def test_diverse_sensor_types(self, sqlite_db, mock_redis):
        """Readings with diverse sensor types merge correctly."""
        from src.services.data_reader import DataReader

        now = int(time.time() * 1000)
        _insert_sv(sqlite_db, now - 60_000, readings={"soil1": 42.0, "light1": 800.0})
        mock_redis.store_reading(now, readings={"soil1": 45.0, "light1": 750.0}, device_id="esp32-1")

        reader = DataReader(mock_redis, sqlite_db)
        readings = reader.get_readings(now - 120_000, now, device_id="esp32-1")

        assert len(readings) == 2
        assert "soil1" in readings[0].readings
        assert "light1" in readings[0].readings
