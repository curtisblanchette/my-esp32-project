"""Unit tests for cortex_memory.py — baseline tracking with Welford's algorithm."""

import math


class TestCortexMemory:
    def test_create_and_read_baseline(self, sqlite_db):
        from src.services.cortex_memory import CortexMemory

        memory = CortexMemory(sqlite_db)
        memory.update_baseline("esp32-1", "temperature", 14, 22.5)

        baseline = memory.get_baseline("esp32-1", "temperature", 14)
        assert baseline is not None
        assert baseline.device_id == "esp32-1"
        assert baseline.sensor == "temperature"
        assert baseline.hour_of_day == 14
        assert baseline.avg_value == 22.5
        assert baseline.sample_count == 1

    def test_incremental_update(self, sqlite_db):
        from src.services.cortex_memory import CortexMemory

        memory = CortexMemory(sqlite_db)
        values = [20.0, 22.0, 24.0]
        for v in values:
            memory.update_baseline("esp32-1", "temperature", 10, v)

        baseline = memory.get_baseline("esp32-1", "temperature", 10)
        assert baseline is not None
        assert baseline.sample_count == 3
        expected_avg = sum(values) / len(values)
        assert abs(baseline.avg_value - expected_avg) < 0.01

    def test_std_dev_calculation(self, sqlite_db):
        from src.services.cortex_memory import CortexMemory

        memory = CortexMemory(sqlite_db)
        values = [20.0, 20.0, 20.0, 20.0, 20.0]
        for v in values:
            memory.update_baseline("esp32-1", "humidity", 8, v)

        baseline = memory.get_baseline("esp32-1", "humidity", 8)
        assert baseline is not None
        assert baseline.std_dev == 0.0  # uniform values → zero std dev

    def test_std_dev_nonzero(self, sqlite_db):
        from src.services.cortex_memory import CortexMemory

        memory = CortexMemory(sqlite_db)
        values = [10.0, 20.0, 30.0]
        for v in values:
            memory.update_baseline("esp32-1", "temperature", 12, v)

        baseline = memory.get_baseline("esp32-1", "temperature", 12)
        assert baseline is not None
        assert baseline.std_dev > 0

    def test_get_baseline_nonexistent(self, sqlite_db):
        from src.services.cortex_memory import CortexMemory

        memory = CortexMemory(sqlite_db)
        assert memory.get_baseline("no-device", "temperature", 0) is None

    def test_deviation_requires_min_samples(self, sqlite_db):
        from src.services.cortex_memory import CortexMemory

        memory = CortexMemory(sqlite_db)
        # Add 5 samples (less than the 10-sample minimum)
        for v in [22.0, 22.5, 23.0, 22.0, 22.5]:
            memory.update_baseline("esp32-1", "temperature", 14, v)

        deviation = memory.get_baseline_deviation("esp32-1", "temperature", 14, 25.0)
        assert deviation is None  # Not enough samples

    def test_deviation_with_enough_samples(self, sqlite_db):
        from src.services.cortex_memory import CortexMemory

        memory = CortexMemory(sqlite_db)
        # Add 15 samples clustered around 22.0
        for v in [22.0, 22.1, 21.9, 22.0, 22.1, 21.9, 22.0, 22.0, 22.1, 21.9, 22.0, 22.1, 21.9, 22.0, 22.0]:
            memory.update_baseline("esp32-1", "temperature", 14, v)

        # Value far from baseline should have high deviation
        deviation = memory.get_baseline_deviation("esp32-1", "temperature", 14, 30.0)
        assert deviation is not None
        assert deviation > 2.0  # Should be many sigma away

    def test_per_hour_isolation(self, sqlite_db):
        from src.services.cortex_memory import CortexMemory

        memory = CortexMemory(sqlite_db)
        memory.update_baseline("esp32-1", "temperature", 8, 18.0)
        memory.update_baseline("esp32-1", "temperature", 14, 28.0)

        b8 = memory.get_baseline("esp32-1", "temperature", 8)
        b14 = memory.get_baseline("esp32-1", "temperature", 14)
        assert b8 is not None and b14 is not None
        assert b8.avg_value == 18.0
        assert b14.avg_value == 28.0

    def test_per_device_isolation(self, sqlite_db):
        from src.services.cortex_memory import CortexMemory

        memory = CortexMemory(sqlite_db)
        memory.update_baseline("esp32-1", "temperature", 10, 20.0)
        memory.update_baseline("esp32-2", "temperature", 10, 30.0)

        b1 = memory.get_baseline("esp32-1", "temperature", 10)
        b2 = memory.get_baseline("esp32-2", "temperature", 10)
        assert b1 is not None and b2 is not None
        assert b1.avg_value == 20.0
        assert b2.avg_value == 30.0
