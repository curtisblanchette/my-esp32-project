"""
Unified data reader — merges Redis (hot) + SQLite (cold) telemetry.

Single source of truth for reading historical sensor data. Used by the
orchestrator's context builder and the intent executor's analyze handler.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .redis_client import RedisClient
    from .sqlite_client import SqliteClient

logger = logging.getLogger(__name__)


@dataclass
class MergedReading:
    ts: int
    readings: dict[str, float] = field(default_factory=dict)  # sensor_id -> value
    device_id: str = ""


class DataReader:
    """Reads and merges telemetry from Redis (hot, <48h) and SQLite (cold)."""

    def __init__(self, redis: "RedisClient", sqlite: "SqliteClient"):
        self._redis = redis
        self._sqlite = sqlite

    def get_readings(
        self,
        since_ms: int,
        until_ms: int | None = None,
        device_id: str | None = None,
        limit: int = 5000,
    ) -> list[MergedReading]:
        """
        Fetch and merge readings from both Redis and SQLite.
        Returns sorted by timestamp ascending, deduplicated by (ts, device_id).
        """
        until_ms = until_ms or int(time.time() * 1000)

        redis_readings = self._redis.get_readings_in_range(since_ms, until_ms, device_id)

        # Query from new sensor_values table
        sv_rows = self._sqlite.query_sensor_values(since_ms, until_ms, device_id=device_id, limit=limit * 10)

        # Group SQLite sensor_values by (ts, device_id)
        sqlite_groups: dict[tuple[int, str], dict[str, float]] = defaultdict(dict)
        for sv in sv_rows:
            sqlite_groups[(sv.ts, sv.device_id)][sv.sensor_id] = sv.value

        seen_keys: set[tuple[int, str]] = set()
        merged: list[MergedReading] = []

        # SQLite first (cold, authoritative for older data)
        for (ts, dev_id), readings_dict in sqlite_groups.items():
            key = (ts, dev_id)
            if key not in seen_keys:
                seen_keys.add(key)
                merged.append(MergedReading(ts=ts, readings=readings_dict, device_id=dev_id))

        # Redis overlay (hot, recent data)
        for r in redis_readings:
            key = (r.ts, r.device_id)
            if key not in seen_keys:
                seen_keys.add(key)
                merged.append(MergedReading(ts=r.ts, readings=dict(r.readings), device_id=r.device_id))

        merged.sort(key=lambda r: r.ts)
        return merged[:limit]

    def get_recent_readings(
        self,
        window_minutes: int = 30,
        device_id: str | None = None,
    ) -> list[MergedReading]:
        """Convenience: get readings from the last N minutes."""
        now = int(time.time() * 1000)
        since = now - (window_minutes * 60 * 1000)
        return self.get_readings(since, now, device_id)

    def extract_metric(
        self,
        readings: list[MergedReading],
        sensor_id: str,
    ) -> tuple[list[float], list[int]]:
        """Extract a single sensor's values and timestamps from merged readings."""
        values: list[float] = []
        timestamps: list[int] = []
        for r in readings:
            val = r.readings.get(sensor_id)
            if val is not None:
                values.append(val)
                timestamps.append(r.ts)
        return values, timestamps
