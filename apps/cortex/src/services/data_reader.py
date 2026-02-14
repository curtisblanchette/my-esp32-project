"""
Unified data reader — merges Redis (hot) + SQLite (cold) telemetry.

Single source of truth for reading historical sensor data. Used by the
orchestrator's context builder and the intent executor's analyze handler.
"""

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .redis_client import RedisClient
    from .sqlite_client import SqliteClient

logger = logging.getLogger(__name__)


@dataclass
class MergedReading:
    ts: int
    temp: float
    humidity: float
    device_id: str


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
        Returns sorted by timestamp ascending, deduplicated by ts.
        """
        until_ms = until_ms or int(time.time() * 1000)

        redis_readings = self._redis.get_readings_in_range(since_ms, until_ms, device_id)
        sqlite_readings = self._sqlite.query_history_raw(since_ms, until_ms, limit, device_id)

        seen_ts: set[int] = set()
        merged: list[MergedReading] = []

        # SQLite first (cold, authoritative for older data)
        for r in sqlite_readings:
            if r.ts not in seen_ts:
                seen_ts.add(r.ts)
                merged.append(MergedReading(
                    ts=r.ts, temp=r.temp, humidity=r.humidity,
                    device_id=r.device_id or "",
                ))

        # Redis overlay (hot, recent data)
        for r in redis_readings:
            if r.ts not in seen_ts:
                seen_ts.add(r.ts)
                merged.append(MergedReading(
                    ts=r.ts, temp=r.temp, humidity=r.humidity,
                    device_id=r.device_id,
                ))

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
        metric: str,
    ) -> tuple[list[float], list[int]]:
        """Extract a single metric's values and timestamps from merged readings."""
        values: list[float] = []
        timestamps: list[int] = []
        for r in readings:
            if metric == "temperature":
                values.append(r.temp)
            elif metric == "humidity":
                values.append(r.humidity)
            timestamps.append(r.ts)
        return values, timestamps
