"""
Redis client for hot telemetry storage (48-hour TTL).

Ported from apps/api/src/lib/redis.ts — same key format and TTL strategy.
"""

import json
import logging
import time
from dataclasses import dataclass, field

import redis

logger = logging.getLogger(__name__)

TTL_SECONDS = 48 * 60 * 60  # 48 hours


@dataclass
class RedisReading:
    ts: int
    readings: dict[str, float] = field(default_factory=dict)  # sensor_id -> value
    source_topic: str | None = None
    device_id: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "readings": self.readings,
            "sourceTopic": self.source_topic,
            "deviceId": self.device_id,
        }


def _parse_readings_dict(data: dict) -> dict[str, float]:
    """Parse readings from stored JSON — handles both new and legacy formats."""
    if "readings" in data and isinstance(data["readings"], dict):
        return data["readings"]
    # Legacy format: {temp: float, humidity: float}
    result: dict[str, float] = {}
    if "temp" in data:
        result["temp1"] = data["temp"]
    if "humidity" in data:
        result["hum1"] = data["humidity"]
    return result


class RedisClient:
    """Redis client for hot telemetry data with 48-hour TTL."""

    def __init__(self, url: str = "redis://localhost:6379"):
        self._url = url
        self._client: redis.Redis | None = None

    def connect(self) -> None:
        """Connect to Redis."""
        self._client = redis.from_url(self._url, decode_responses=True)
        try:
            self._client.ping()
            logger.info(f"Redis connected: {self._url}")
        except redis.ConnectionError as e:
            logger.error(f"Redis connection failed: {e}")
            raise

    def _get_client(self) -> redis.Redis:
        if self._client is None:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._client

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def store_reading(self, reading: RedisReading) -> None:
        """Store a reading with 48-hour TTL."""
        client = self._get_client()
        key = f"reading:{reading.device_id}:{reading.ts}" if reading.device_id else f"reading:{reading.ts}"
        client.setex(key, TTL_SECONDS, json.dumps(reading.to_dict()))

    def get_readings_in_range(
        self, since_ms: int, until_ms: int, device_id: str | None = None
    ) -> list[RedisReading]:
        """Get readings within a time range, optionally filtered by device."""
        client = self._get_client()
        readings: list[RedisReading] = []

        keys: list[str] = []
        for key in client.scan_iter(match="reading:*", count=100):
            keys.append(key)

        if not keys:
            return readings

        values = client.mget(keys)

        for i, value in enumerate(values):
            if not value:
                continue
            try:
                data = json.loads(value)
                ts = data.get("ts", 0)
                if since_ms <= ts <= until_ms:
                    if device_id and data.get("deviceId") != device_id:
                        continue
                    readings.append(RedisReading(
                        ts=ts,
                        readings=_parse_readings_dict(data),
                        source_topic=data.get("sourceTopic"),
                        device_id=data.get("deviceId", ""),
                    ))
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse reading from key {keys[i]}: {e}")

        readings.sort(key=lambda r: r.ts)
        return readings

    def get_all_readings(self) -> list[RedisReading]:
        """Get all readings from Redis."""
        client = self._get_client()
        readings: list[RedisReading] = []

        keys: list[str] = []
        for key in client.scan_iter(match="reading:*", count=100):
            keys.append(key)

        if not keys:
            return readings

        values = client.mget(keys)

        for i, value in enumerate(values):
            if not value:
                continue
            try:
                data = json.loads(value)
                readings.append(RedisReading(
                    ts=data["ts"],
                    readings=_parse_readings_dict(data),
                    source_topic=data.get("sourceTopic"),
                    device_id=data.get("deviceId", ""),
                ))
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse reading from key {keys[i]}: {e}")

        readings.sort(key=lambda r: r.ts)
        return readings

    def delete_readings(self, keys: list[str]) -> None:
        """Delete readings by key."""
        if not keys:
            return
        client = self._get_client()
        client.delete(*keys)
