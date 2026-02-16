"""
Intent executor — shared handler for chat + voice commands.

Ported from apps/api/src/routes/utils/executeIntent.ts.
"""

import asyncio
import logging
import sqlite3
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
    from .chat_session import ChatSessionStore
    from .data_reader import DataReader
    from .decision_engine import DecisionEngine
    from .mqtt_client import MqttService
    from .redis_client import RedisClient
    from .rule_generator import RuleGenerator
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
    session_store: "ChatSessionStore | None" = None,
    session_id: str | None = None,
    rule_generator: "RuleGenerator | None" = None,
    engine: "DecisionEngine | None" = None,
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

    if intent_type == "generate_rules":
        loc = intent.get("location") or location
        goal = intent.get("goal", message)

        if not loc:
            # List available locations for the user to choose
            devices = sqlite.get_all_devices()
            locations = sorted({d.location for d in devices})
            if locations:
                loc_list = ", ".join(locations)
                return {
                    "ok": True,
                    "reply": f"Which location should I set up rules for? I see devices at: {loc_list}",
                }
            return {
                "ok": True,
                "reply": "No devices are registered yet. Connect a device first, then I can help set up rules.",
            }

        if not rule_generator:
            return {
                "ok": False,
                "reply": "Rule generation is not available right now.",
            }

        result = await asyncio.to_thread(rule_generator.generate_rules, loc, goal)

        if result["error"]:
            return {"ok": False, "reply": result["error"]}

        # Store in session for approval
        if session_store and session_id:
            session_store.set_proposed_rules(session_id, result["rules"])
            session = session_store.get_or_create(session_id)
            session.location = loc
            session.goal = goal

        # Save the location goal
        sqlite.upsert_location_goal(loc, goal)

        rules_display = _format_proposed_rules(result["rules"])
        explanation = result.get("explanation", "")
        reply_parts = [intent.get("reply", f"I've generated {len(result['rules'])} rules for {loc}.")]
        if explanation:
            reply_parts.append(explanation)
        reply_parts.append(rules_display)
        reply_parts.append("Say **approve** to activate these rules, or tell me what to change.")

        return {
            "ok": True,
            "reply": "\n\n".join(reply_parts),
            "action": {
                "type": "proposed_rules",
                "rules": result["rules"],
                "location": loc,
                "goal": goal,
            },
        }

    if intent_type == "approve_rules":
        if not session_store or not session_id:
            return {
                "ok": True,
                "reply": "I don't have any pending rules to approve. Try describing your goals first.",
            }

        proposed = session_store.get_proposed_rules(session_id)
        if not proposed:
            return {
                "ok": True,
                "reply": "No rules are pending approval. Describe your goals to generate new ones.",
            }

        session = session_store.get_or_create(session_id)
        inserted = []
        skipped = []

        for rule_dict in proposed:
            try:
                rule = sqlite.insert_rule(
                    name=rule_dict["name"],
                    description=rule_dict["description"],
                    condition=rule_dict["condition"],
                    action=rule_dict["action"],
                    enabled=True,
                    source="generated",
                )
                inserted.append(rule)
            except sqlite3.IntegrityError:
                skipped.append(rule_dict["name"])

        # Reload decision engine
        if engine:
            engine.reload_from_sqlite(sqlite)

        # Broadcast updated rules
        if ws:
            await ws.broadcast_rules(sqlite.get_all_rules())

        session_store.clear_proposed_rules(session_id)

        reply = intent.get("reply", "")
        if inserted:
            reply = f"{len(inserted)} rules are now active"
            if session.location:
                reply += f" for {session.location}"
            reply += ". They'll start evaluating immediately."
        if skipped:
            reply += f" ({len(skipped)} skipped due to name conflicts: {', '.join(skipped)})"

        return {
            "ok": True,
            "reply": reply,
            "action": {"type": "rules_activated", "count": len(inserted)},
        }

    if intent_type == "refine_rules":
        if not session_store or not session_id:
            return {
                "ok": True,
                "reply": "I don't have rules to refine. Describe your goals first.",
            }

        proposed = session_store.get_proposed_rules(session_id)
        session = session_store.get_or_create(session_id)

        if not proposed or not session.location:
            return {
                "ok": True,
                "reply": "No rules pending. Describe your goals to generate new ones.",
            }

        if not rule_generator:
            return {
                "ok": False,
                "reply": "Rule generation is not available right now.",
            }

        refinement = intent.get("refinement", message)
        result = await asyncio.to_thread(
            rule_generator.refine_rules,
            proposed,
            refinement,
            session.location,
            session.goal or "",
        )

        if result["error"]:
            return {"ok": False, "reply": f"I had trouble refining: {result['error']}"}

        session_store.set_proposed_rules(session_id, result["rules"])

        rules_display = _format_proposed_rules(result["rules"])
        reply = intent.get("reply", "Here are the updated rules:")

        return {
            "ok": True,
            "reply": f"{reply}\n\n{rules_display}\n\nSay **approve** to activate, or tell me more changes.",
            "action": {"type": "proposed_rules", "rules": result["rules"]},
        }

    # intent === "none"
    return {
        "ok": True,
        "reply": intent.get("reply", "I'm not sure how to help with that."),
    }


def _format_proposed_rules(rules: list[dict]) -> str:
    """Format proposed rules for chat display."""
    if not rules:
        return "No rules generated."

    lines = []
    for i, rule in enumerate(rules, 1):
        cond = rule.get("condition", {})
        act = rule.get("action", {})

        sensor_id = cond.get("sensor", "")
        stype = guess_sensor_type(sensor_id)
        slabel = sensor_label(stype)
        unit = sensor_unit(stype)

        # Build condition description
        cond_parts = [f"{slabel} {cond.get('operator', '>')} {cond.get('threshold', '?')}{unit}"]
        if cond.get("duration_seconds"):
            cond_parts.append(f"for {cond['duration_seconds']}s")
        if cond.get("trend"):
            cond_parts.append(f"trending {cond['trend']}")
        if cond.get("forecast"):
            forecast_label = "will exceed" if cond["forecast"] == "will_exceed" else "will drop below"
            cond_parts.append(f"{forecast_label} {cond.get('forecast_threshold', '?')}{unit}")
        if cond.get("time_of_day"):
            tod = cond["time_of_day"]
            cond_parts.append(f"between {tod.get('after', '?')} and {tod.get('before', '?')}")

        # Build action description
        target = act.get("target", "?")
        if act.get("action") == "pulse":
            action_str = f"{target} PULSE"
        else:
            value = "ON" if act.get("value") else "OFF"
            action_str = f"{target} = {value}"

        lines.append(f"**Rule {i}: {rule.get('description', rule.get('name', ''))}**")
        lines.append(f"  When {' '.join(cond_parts)}")
        lines.append(f"  Then: {action_str}")
        lines.append("")

    return "\n".join(lines)
