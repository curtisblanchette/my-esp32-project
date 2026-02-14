"""Unit tests for data_reader.py — unified Redis+SQLite merge layer."""

import time

from src.services.sqlite_client import TelemetryRow


def _insert_sqlite(db, ts, temp, humidity, device_id="esp32-1"):
    db.insert_reading(TelemetryRow(ts=ts, temp=temp, humidity=humidity, source_topic="mqtt", device_id=device_id))


class TestDataReader:
    def test_merge_redis_and_sqlite(self, sqlite_db, mock_redis):
        from src.services.data_reader import DataReader

        now = int(time.time() * 1000)

        # Insert into SQLite (older data)
        _insert_sqlite(sqlite_db, now - 120_000, 22.0, 55.0)
        _insert_sqlite(sqlite_db, now - 60_000, 22.5, 54.0)

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
        _insert_sqlite(sqlite_db, ts, 22.0, 55.0)
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

    def test_extract_metric_temperature(self, sqlite_db, mock_redis):
        from src.services.data_reader import DataReader, MergedReading

        reader = DataReader(mock_redis, sqlite_db)
        readings = [
            MergedReading(ts=1000, temp=22.0, humidity=55.0, device_id="esp32-1"),
            MergedReading(ts=2000, temp=23.0, humidity=54.0, device_id="esp32-1"),
        ]

        values, timestamps = reader.extract_metric(readings, "temperature")
        assert values == [22.0, 23.0]
        assert timestamps == [1000, 2000]

    def test_extract_metric_humidity(self, sqlite_db, mock_redis):
        from src.services.data_reader import DataReader, MergedReading

        reader = DataReader(mock_redis, sqlite_db)
        readings = [
            MergedReading(ts=1000, temp=22.0, humidity=55.0, device_id="esp32-1"),
            MergedReading(ts=2000, temp=23.0, humidity=54.0, device_id="esp32-1"),
        ]

        values, timestamps = reader.extract_metric(readings, "humidity")
        assert values == [55.0, 54.0]
        assert timestamps == [1000, 2000]

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
