"""
Intent executor — shared handler for chat + voice commands.

Ported from apps/api/src/routes/utils/executeIntent.ts.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, TYPE_CHECKING

from .analysis import (
    analyze_sensor_data,
    format_analysis_reply,
    format_history_reply,
    parse_timeframe,
)
from .ollama_client import OllamaIntent
from .sensor_meta import guess_sensor_type, sensor_label, sensor_unit

if TYPE_CHECKING:
    from .data_reader import DataReader
    from .mqtt_client import MqttService
    from .redis_client import RedisClient
    from .sqlite_client import SqliteClient
    from .websocket_server import WebSocketServer

logger = logging.getLogger(__name__)


def resolve_device(
    intent: OllamaIntent,
    ctx_device_id: str | None,
    ctx_location: str | None,
    sqlite: "SqliteClient",
) -> tuple[str, str]:
    """Resolve device ID and location from intent + context."""
    device_id = intent.get("deviceId") or ctx_device_id or "esp32-1"
    device = sqlite.get_device(device_id)
    location = ctx_location or (device.location if device else "room1")
    return device_id, location


async def execute_intent(
    intent: OllamaIntent,
    source: str,
    message: str,
    sqlite: "SqliteClient",
    redis: "RedisClient",
    mqtt: "MqttService",
    ws: "WebSocketServer",
    device_id: str | None = None,
    location: str | None = None,
    data_reader: "DataReader | None" = None,
) -> dict[str, Any]:
    """
    Execute a parsed intent.

    Returns:
        {"ok": bool, "reply": str, "action"?: {...}}
    """
    intent_type = intent.get("intent")

    if intent_type == "command":
        resolved_id, resolved_loc = resolve_device(intent, device_id, location, sqlite)

        correlation_id = f"cmd-{uuid.uuid4().hex[:8]}"
        now = int(time.time() * 1000)

        # Publish MQTT command
        topic = f"home/{resolved_loc}/{resolved_id}/command"
        envelope = {
            "v": 1,
            "ts": now,
            "correlationId": correlation_id,
            "source": source,
            "deviceId": resolved_id,
            "location": resolved_loc,
            "type": "command",
            "payload": {
                "target": intent["target"],
                "action": intent["action"],
                "value": intent["value"],
                "reason": f"{'Voice' if source == 'voice' else 'Chat'} command: \"{message}\"",
                "ttl": 30000,
            },
        }

        if not mqtt or not mqtt.is_connected:
            return {
                "ok": False,
                "reply": "I understood your request, but the device is currently unreachable.",
            }

        mqtt.publish_json(topic, envelope)

        # Store command in SQLite
        reason = f"{'Voice' if source == 'voice' else 'Chat'} command: \"{message}\""
        cmd = sqlite.insert_command(
            id=correlation_id,
            ts=now,
            device_id=resolved_id,
            target=intent["target"],
            action=intent["action"],
            value=intent["value"],
            source=source,
            reason=reason,
        )
        await ws.broadcast_command(sqlite.command_to_dict(cmd))

        return {
            "ok": True,
            "reply": intent["reply"],
            "action": {
                "type": "command",
                "correlationId": correlation_id,
                "target": intent["target"],
                "value": intent["value"],
            },
        }

    if intent_type == "query":
        resolved_id, _ = resolve_device(intent, device_id, location, sqlite)
        latest = ws.get_latest_by_device(resolved_id)
        sensor_value = None

        if latest:
            sensor = intent.get("sensor", "")
            readings = latest.get("readings", {})
            sensor_value = readings.get(sensor)

        return {
            "ok": True,
            "reply": intent["reply"],
            "action": {
                "type": "query",
                "sensor": intent.get("sensor"),
                "value": sensor_value,
            },
        }

    if intent_type == "history":
        timeframe = intent.get("timeframe", "24h")
        since_ms = int(time.time() * 1000) - parse_timeframe(timeframe)
        category = intent.get("category", "all")

        commands_list = None
        events_list = None

        if category in ("commands", "all"):
            cmds = sqlite.query_commands(since_ms=since_ms, limit=50)
            commands_list = [sqlite.command_to_dict(c) for c in cmds]
        if category in ("events", "all"):
            evts = sqlite.query_events(since_ms=since_ms, limit=50)
            events_list = [sqlite.event_to_dict(e) for e in evts]

        formatted = format_history_reply(
            reply=intent["reply"],
            summary=intent.get("summary"),
            commands=commands_list,
            events=events_list,
            category=category,
        )

        return {
            "ok": True,
            "reply": formatted,
            "action": {
                "type": "history",
                "timeframe": timeframe,
                "category": category,
                "commands": commands_list,
                "events": events_list,
            },
        }

    if intent_type == "analyze":
        timeframe = intent.get("timeframe", "24h")
        metric = intent.get("metric", "all")
        since_ms = int(time.time() * 1000) - parse_timeframe(timeframe)
        until_ms = int(time.time() * 1000)

        try:
            if data_reader:
                merged = data_reader.get_readings(since_ms, until_ms)
            else:
                # Fallback: build MergedReading from Redis
                from .data_reader import MergedReading
                redis_readings = redis.get_readings_in_range(since_ms, until_ms)
                merged = [
                    MergedReading(ts=r.ts, readings=dict(r.readings), device_id=r.device_id)
                    for r in redis_readings
                ]

            analysis = analyze_sensor_data(metric, merged)
            formatted = format_analysis_reply(
                reply=intent["reply"],
                summary=intent.get("summary"),
                analysis=analysis,
            )

            return {
                "ok": True,
                "reply": formatted,
                "action": {
                    "type": "analyze",
                    "timeframe": timeframe,
                    "metric": metric,
                    "analysis": analysis,
                },
            }
        except Exception as e:
            logger.error(f"Error analyzing sensor data: {e}")
            return {
                "ok": False,
                "reply": "I encountered an error while analyzing the sensor data. Please try again.",
            }

    # intent === "none"
    return {
        "ok": True,
        "reply": intent.get("reply", "I'm not sure how to help with that."),
    }
