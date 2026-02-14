"""
Telemetry routes: GET /api/latest, GET /api/history

Ported from apps/api/src/routes/telemetry.ts
"""

import math
import time
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter()


def create_telemetry_router(sqlite, redis_client, ws_server) -> APIRouter:
    r = APIRouter()

    @r.get("/latest")
    async def get_latest():
        latest = ws_server.get_all_latest_by_device()
        # Return the most recent reading across all devices
        if latest:
            most_recent = max(latest.values(), key=lambda x: x.get("updatedAt", 0))
            return {"ok": True, "latest": most_recent}
        return {"ok": True, "latest": None}

    @r.get("/history")
    async def get_history(
        sinceMs: int = Query(...),
        untilMs: int | None = Query(None),
        limit: int = Query(5000),
        bucketMs: int | None = Query(None),
        deviceId: str | None = Query(None),
    ):
        until = untilMs if untilMs is not None else int(time.time() * 1000)

        if limit >= 10000:
            limit = 5000
        if limit <= 0:
            return {"ok": False, "error": "limit must be a positive number"}

        try:
            redis_readings = redis_client.get_readings_in_range(sinceMs, until, deviceId)

            if bucketMs is not None:
                if bucketMs <= 0:
                    return {"ok": False, "error": "bucketMs must be a positive number"}

                bucketed_sqlite = sqlite.query_history_bucketed(
                    since_ms=sinceMs, until_ms=until, bucket_ms=bucketMs,
                    limit=limit, device_id=deviceId,
                )

                # Merge SQLite buckets and Redis readings into unified buckets
                buckets: dict[int, dict[str, float]] = {}

                for row in bucketed_sqlite:
                    buckets[row.ts] = {
                        "tempSum": row.temp * row.count,
                        "humiditySum": row.humidity * row.count,
                        "count": row.count,
                    }

                for reading in redis_readings:
                    bucket_ts = (reading.ts // bucketMs) * bucketMs
                    if bucket_ts in buckets:
                        buckets[bucket_ts]["tempSum"] += reading.temp
                        buckets[bucket_ts]["humiditySum"] += reading.humidity
                        buckets[bucket_ts]["count"] += 1
                    else:
                        buckets[bucket_ts] = {
                            "tempSum": reading.temp,
                            "humiditySum": reading.humidity,
                            "count": 1,
                        }

                points = [
                    {
                        "ts": ts,
                        "temp": b["tempSum"] / b["count"],
                        "humidity": b["humiditySum"] / b["count"],
                        "count": int(b["count"]),
                    }
                    for ts, b in sorted(buckets.items())
                ]
                points = points[-limit:]

                return {
                    "ok": True, "mode": "bucketed", "points": points,
                    "sources": {"redis": len(redis_readings), "sqlite": len(bucketed_sqlite)},
                    "deviceId": deviceId,
                }

            # Raw mode
            sqlite_readings = sqlite.query_history_raw(
                since_ms=sinceMs, until_ms=until, limit=limit, device_id=deviceId,
            )

            all_readings = [
                {"ts": r.ts, "temp": r.temp, "humidity": r.humidity,
                 "sourceTopic": r.source_topic, "deviceId": r.device_id}
                for r in sqlite_readings
            ] + [
                {"ts": r.ts, "temp": r.temp, "humidity": r.humidity,
                 "sourceTopic": r.source_topic, "deviceId": r.device_id}
                for r in redis_readings
            ]
            all_readings.sort(key=lambda x: x["ts"])
            all_readings = all_readings[-limit:]

            return {
                "ok": True, "mode": "raw", "points": all_readings,
                "sources": {"redis": len(redis_readings), "sqlite": len(sqlite_readings)},
                "deviceId": deviceId,
            }

        except Exception as e:
            return {"ok": False, "error": f"Failed to query history: {e}"}

    return r
