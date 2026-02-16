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

                bucketed_sqlite = sqlite.query_sensor_values_bucketed(
                    since_ms=sinceMs, until_ms=until, bucket_ms=bucketMs,
                    limit=limit * 10, device_id=deviceId,
                )

                # Merge SQLite buckets and Redis readings into unified per-sensor buckets
                # Key: (bucket_ts, sensor_id) → {sum, count}
                from collections import defaultdict
                buckets: dict[tuple[int, str], dict] = {}

                for row in bucketed_sqlite:
                    key = (row["ts"], row["sensor_id"])
                    buckets[key] = {"sum": row["value"] * row["count"], "count": row["count"]}

                for reading in redis_readings:
                    bucket_ts = (reading.ts // bucketMs) * bucketMs
                    for sensor_id, value in reading.readings.items():
                        key = (bucket_ts, sensor_id)
                        if key in buckets:
                            buckets[key]["sum"] += value
                            buckets[key]["count"] += 1
                        else:
                            buckets[key] = {"sum": value, "count": 1}

                # Group by bucket_ts into {ts, readings: {sensor_id: avg_value}, count}
                ts_groups: dict[int, dict] = {}
                for (ts, sensor_id), data in sorted(buckets.items()):
                    if ts not in ts_groups:
                        ts_groups[ts] = {"ts": ts, "readings": {}, "count": 0}
                    avg = data["sum"] / data["count"]
                    ts_groups[ts]["readings"][sensor_id] = round(avg, 2)
                    ts_groups[ts]["count"] = max(ts_groups[ts]["count"], data["count"])

                points = list(ts_groups.values())
                points.sort(key=lambda p: p["ts"])
                points = points[-limit:]

                return {
                    "ok": True, "mode": "bucketed", "points": points,
                    "sources": {"redis": len(redis_readings), "sqlite": len(bucketed_sqlite)},
                    "deviceId": deviceId,
                }

            # Raw mode — query from sensor_values table
            from collections import defaultdict as _dd
            sv_rows = sqlite.query_sensor_values(
                since_ms=sinceMs, until_ms=until, limit=limit * 10, device_id=deviceId,
            )

            # Group by (ts, device_id)
            groups: dict[tuple[int, str], dict[str, float]] = {}
            for sv in sv_rows:
                key = (sv.ts, sv.device_id)
                if key not in groups:
                    groups[key] = {}
                groups[key][sv.sensor_id] = sv.value

            all_readings = [
                {"ts": ts, "readings": readings, "deviceId": dev_id}
                for (ts, dev_id), readings in groups.items()
            ]

            # Add Redis readings
            for r in redis_readings:
                all_readings.append({
                    "ts": r.ts,
                    "readings": dict(r.readings),
                    "deviceId": r.device_id,
                })

            all_readings.sort(key=lambda x: x["ts"])
            all_readings = all_readings[-limit:]

            return {
                "ok": True, "mode": "raw", "points": all_readings,
                "sources": {"redis": len(redis_readings), "sqlite": len(sv_rows)},
                "deviceId": deviceId,
            }

        except Exception as e:
            return {"ok": False, "error": f"Failed to query history: {e}"}

    @r.get("/outcomes")
    async def get_outcomes(
        deviceId: str | None = Query(None),
        target: str | None = Query(None),
        sinceMs: int | None = Query(None),
        limit: int = Query(50),
    ):
        try:
            import json as _json
            db = sqlite._get_db()

            sql = ("SELECT correlation_id, device_id, target, action, value, reason, "
                   "command_ts, ack_status, target_metric, desired_direction, "
                   "pre_value, post_1m, post_5m, post_10m, effectiveness, scored_at "
                   "FROM cortex_outcomes WHERE 1=1")
            params: list[Any] = []

            if deviceId:
                sql += " AND device_id = ?"
                params.append(deviceId)
            if target:
                sql += " AND target = ?"
                params.append(target)
            if sinceMs:
                sql += " AND command_ts >= ?"
                params.append(sinceMs)

            sql += " ORDER BY command_ts DESC LIMIT ?"
            params.append(limit)

            cursor = db.execute(sql, params)
            outcomes = [
                {
                    "correlationId": row["correlation_id"],
                    "deviceId": row["device_id"],
                    "target": row["target"],
                    "action": row["action"],
                    "value": _json.loads(row["value"]) if row["value"] else None,
                    "reason": row["reason"],
                    "commandTs": row["command_ts"],
                    "ackStatus": row["ack_status"],
                    "targetMetric": row["target_metric"],
                    "desiredDirection": row["desired_direction"],
                    "preValue": row["pre_value"],
                    "post1m": row["post_1m"],
                    "post5m": row["post_5m"],
                    "post10m": row["post_10m"],
                    "effectiveness": row["effectiveness"],
                    "scoredAt": row["scored_at"],
                }
                for row in cursor.fetchall()
            ]
            return {"ok": True, "outcomes": outcomes}
        except Exception as e:
            return {"ok": False, "error": f"Failed to query outcomes: {e}"}

    return r
