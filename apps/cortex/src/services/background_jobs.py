"""
Background jobs — aggregation (Redis → SQLite), command expiration, rule advisor.

Ported from apps/api/src/services/aggregationJob.ts and commandExpirationJob.ts.
"""

import asyncio
import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .redis_client import RedisClient
    from .sqlite_client import SqliteClient
    from .websocket_server import WebSocketServer
    from .rule_advisor import RuleAdvisor

logger = logging.getLogger(__name__)

ADVISOR_INTERVAL_S = 6 * 60 * 60  # 6 hours
ADVISOR_INITIAL_DELAY_S = 60      # 1 minute after startup
AGGREGATION_INTERVAL_S = 10 * 60  # 10 minutes
BUCKET_SIZE_MS = 5 * 60 * 1000    # 5 minutes
EXPIRATION_CHECK_INTERVAL_S = 5   # 5 seconds


async def start_aggregation_job(
    redis: "RedisClient",
    sqlite: "SqliteClient",
) -> None:
    """Periodically aggregate Redis readings into SQLite buckets."""
    logger.info(f"Starting aggregation job (interval: {AGGREGATION_INTERVAL_S}s, bucket: {BUCKET_SIZE_MS / 1000}s)")

    # Initial run after short delay
    await asyncio.sleep(5)
    await _aggregate_and_flush(redis, sqlite)

    while True:
        await asyncio.sleep(AGGREGATION_INTERVAL_S)
        await _aggregate_and_flush(redis, sqlite)


async def _aggregate_and_flush(redis: "RedisClient", sqlite: "SqliteClient") -> None:
    """Read all Redis readings, bucket-average them, write to SQLite, delete from Redis."""
    try:
        readings = redis.get_all_readings()

        if not readings:
            logger.debug("No readings to aggregate")
            return

        logger.info(f"Aggregating {len(readings)} readings from Redis")

        # Group into time buckets, keyed by (bucket_ts, device_id)
        buckets: dict[tuple[int, str], dict] = {}

        for reading in readings:
            bucket_ts = (reading.ts // BUCKET_SIZE_MS) * BUCKET_SIZE_MS
            key = (bucket_ts, reading.device_id)

            if key in buckets:
                buckets[key]["temp_sum"] += reading.temp
                buckets[key]["humidity_sum"] += reading.humidity
                buckets[key]["count"] += 1
            else:
                buckets[key] = {
                    "ts": bucket_ts,
                    "temp_sum": reading.temp,
                    "humidity_sum": reading.humidity,
                    "count": 1,
                    "source_topic": reading.source_topic,
                    "device_id": reading.device_id,
                }

        from .sqlite_client import TelemetryRow

        inserted = 0
        for bucket in buckets.values():
            avg_temp = round(bucket["temp_sum"] / bucket["count"], 2)
            avg_humidity = round(bucket["humidity_sum"] / bucket["count"], 2)

            sqlite.insert_reading(TelemetryRow(
                ts=bucket["ts"],
                temp=avg_temp,
                humidity=avg_humidity,
                source_topic=bucket["source_topic"],
                device_id=bucket["device_id"],
            ))
            inserted += 1

        logger.info(f"Flushed {inserted} aggregated buckets to SQLite (from {len(readings)} readings)")

        # Delete from Redis
        keys_to_delete = [
            f"reading:{r.device_id}:{r.ts}" if r.device_id else f"reading:{r.ts}"
            for r in readings
        ]
        redis.delete_readings(keys_to_delete)
        logger.info(f"Deleted {len(keys_to_delete)} readings from Redis")

    except Exception as e:
        logger.error(f"Aggregation job failed: {e}")


async def start_command_expiration_job(
    sqlite: "SqliteClient",
    ws: "WebSocketServer",
) -> None:
    """Periodically expire pending commands that have exceeded their TTL."""
    logger.info(f"Starting command expiration job (interval: {EXPIRATION_CHECK_INTERVAL_S}s)")

    # Initial run after short delay
    await asyncio.sleep(2)
    await _check_and_expire(sqlite, ws)

    while True:
        await asyncio.sleep(EXPIRATION_CHECK_INTERVAL_S)
        await _check_and_expire(sqlite, ws)


async def _check_and_expire(sqlite: "SqliteClient", ws: "WebSocketServer") -> None:
    """Expire pending commands and broadcast status updates."""
    try:
        expired = sqlite.expire_commands()
        if expired:
            logger.info(f"Expired {len(expired)} command(s): {', '.join(c.id for c in expired)}")
            for cmd in expired:
                await ws.broadcast_command(sqlite.command_to_dict(cmd))
    except Exception as e:
        logger.error(f"Command expiration job failed: {e}")


async def start_rule_advisor_job(
    rule_advisor: "RuleAdvisor",
    sqlite: "SqliteClient | None" = None,
    ws: "WebSocketServer | None" = None,
) -> None:
    """Periodically run the Rule Advisor to suggest rule improvements."""
    logger.info(f"Starting rule advisor job (interval: {ADVISOR_INTERVAL_S}s)")
    await asyncio.sleep(ADVISOR_INITIAL_DELAY_S)

    while True:
        try:
            suggestions = await asyncio.to_thread(rule_advisor.analyze)
            if suggestions:
                logger.info(f"Rule advisor produced {len(suggestions)} suggestion(s)")
                for s in suggestions:
                    action = "auto-applied" if s["status"] == "applied" else "pending review"
                    logger.info(
                        f"  [{action}] {s['ruleName']}.{s['field']}: "
                        f"{s['currentValue']} → {s['suggestedValue']} "
                        f"(confidence: {s['confidence']:.0%})"
                    )
                # Broadcast updated suggestions to all WebSocket clients
                if ws and sqlite:
                    all_suggestions = sqlite.get_suggestions(limit=20)
                    await ws.broadcast_suggestions(all_suggestions)
            else:
                logger.info("Rule advisor: no suggestions this cycle")
        except Exception as e:
            logger.error(f"Rule advisor job failed: {e}")

        await asyncio.sleep(ADVISOR_INTERVAL_S)
