"""LLM-powered rule generation from device capabilities and user goals."""

import json
import logging
from typing import TYPE_CHECKING, Any

from .sensor_meta import sensor_unit, guess_sensor_type

if TYPE_CHECKING:
    from .cortex_memory import CortexMemory
    from .ollama_client import OllamaClient
    from .sqlite_client import Sensor, SqliteClient
    from .websocket_server import WebSocketServer

logger = logging.getLogger(__name__)

VALID_OPERATORS = {">", "<", ">=", "<=", "==", "!="}
VALID_TRENDS = {"rising", "falling", "stable"}
VALID_FORECASTS = {"will_exceed", "will_drop_below"}
VALID_SCOPES = {"self", "any", "all"}

# Keys in the latest-reading dict that are metadata, not sensor values.
_METADATA_KEYS = {"updatedAt", "sourceTopic", "deviceId", "sourceIp"}

# Legacy reading-key → sensor-ID bridge (until telemetry pipeline goes generic).
_LEGACY_MAP = {"temp": "temp1", "humidity": "hum1"}


def _resolve_reading_unit(reading_key: str, sensor_map: dict[str, "Sensor"]) -> str:
    """Resolve the display unit for a reading key using device sensor metadata."""
    # Direct match: reading key is a sensor ID (future generic format)
    if reading_key in sensor_map:
        s = sensor_map[reading_key]
        return sensor_unit(s.type, s.unit)
    # Legacy: "temp" → temp1, "humidity" → hum1
    mapped_id = _LEGACY_MAP.get(reading_key)
    if mapped_id and mapped_id in sensor_map:
        s = sensor_map[mapped_id]
        return sensor_unit(s.type, s.unit)
    # Fallback: guess from key prefix
    return sensor_unit(guess_sensor_type(reading_key))

RULE_GENERATION_SYSTEM_PROMPT = """\
You are generating automation rules for an IoT sensor monitoring system.
Given a user's goal for a location and the available devices/capabilities/baselines, generate a set of rules.

Each rule must be a JSON object with this exact structure:
{
  "name": "descriptive_snake_case_name",
  "description": "Human-readable description of what this rule does",
  "condition": {
    "sensor": "<sensor_id>",
    "operator": "<op>",
    "threshold": <number>,
    "duration_seconds": <int>,
    "trend": "rising" | "falling" | null,
    "time_of_day": {"after": "HH:MM", "before": "HH:MM"} | null,
    "forecast": "will_exceed" | "will_drop_below" | null,
    "forecast_threshold": <number> | null,
    "forecast_within_minutes": <number>,
    "baseline_deviation": <number> | null
  },
  "action": {
    "target": "<actuator_id>",
    "action": "set" | "pulse",
    "value": true | false,
    "reason": "Brief explanation of why this action is taken"
  }
}

Guidelines:
- Only use sensor IDs and actuator IDs that exist on the devices at this location
- Set thresholds appropriate for the stated goal and sensor type
- Use learned baselines when available to set realistic thresholds
- Include both "action" and "restore" rules (e.g., fan ON when hot, fan OFF when cooled)
- Include trend-aware rules where appropriate (preemptive action on rising/falling trends)
- Include forecast rules for gradual environmental changes
- For binary/contact sensors (contact, motion), use == operator with 0 or 1
- For momentary actuators (garage doors, buzzers), use "action": "pulse" instead of "set"
- duration_seconds should be at least 30 to avoid rapid toggling
- Be conservative — fewer good rules are better than many noisy ones
- Each rule name must be unique and descriptive using snake_case
- Omit optional fields (trend, time_of_day, forecast, baseline_deviation) if not needed — do not include null values

Example rule sets by domain:

Grow room (temp + humidity + soil moisture + switch actuators):
- temp1 > 28 for 60s → exhaust1 ON (ventilate when too warm)
- temp1 < 24 for 60s → exhaust1 OFF (restore when cooled)
- soil1 < 60 for 120s → drip1 ON (water when soil is dry)
- soil1 > 75 for 60s → drip1 OFF (stop watering when saturated)
- hum1 > 55 trending rising → exhaust1 ON (preemptive dehumidify)
- light1 ON between 06:00-18:00 via time_of_day (photoperiod scheduling)

Garage/security (contact sensor + camera + momentary actuators):
- contact1 == 1 for 600s → garage1 pulse (auto-close door after 10 min open)
- contact1 == 1 for 0s → lights1 ON (lights on when door opens)
- contact1 == 0 for 120s → lights1 OFF (lights off 2 min after close)

Respond with valid JSON only:
{"rules": [...], "explanation": "Brief summary of the rule set and reasoning"}
"""

RULE_REFINEMENT_SYSTEM_PROMPT = """\
You are refining automation rules for an IoT sensor monitoring system.
The user has reviewed a set of proposed rules and wants changes.

You will receive:
1. The current proposed rules
2. The user's requested changes
3. The device context (capabilities, baselines)

Apply the requested changes and return the full updated rule set.
Keep rules that weren't mentioned in the changes.
Follow the same JSON format as the original rules.

Respond with valid JSON only:
{"rules": [...], "explanation": "Brief summary of what changed"}
"""


class RuleGenerator:
    """Generates automation rules using LLM based on user goals and device context."""

    def __init__(
        self,
        sqlite: "SqliteClient",
        memory: "CortexMemory",
        ollama: "OllamaClient",
        ws: "WebSocketServer | None" = None,
    ) -> None:
        self._sqlite = sqlite
        self._memory = memory
        self._ollama = ollama
        self._ws = ws

    def generate_rules(self, location: str, goal: str) -> dict[str, Any]:
        """Generate rules for a location based on a goal.

        Returns {"rules": [...], "explanation": "...", "error": None}
        or {"rules": [], "explanation": "", "error": "error message"}
        """
        context = self._build_generation_context(location, goal)

        if not context["devices"]:
            return {
                "rules": [],
                "explanation": "",
                "error": f"No devices found at location '{location}'",
            }

        if not context["actuators"]:
            return {
                "rules": [],
                "explanation": "",
                "error": f"No actuators found at location '{location}' — rules need actuators to control",
            }

        prompt = self._format_generation_prompt(context)

        try:
            raw = self._ollama.generate(
                prompt, system=RULE_GENERATION_SYSTEM_PROMPT, format="json"
            )
            result = self._parse_and_validate(raw, context)
            return result
        except Exception as e:
            logger.error(f"Rule generation failed: {e}")
            return {
                "rules": [],
                "explanation": "",
                "error": f"Rule generation failed: {e}",
            }

    def refine_rules(
        self,
        current_rules: list[dict],
        refinement: str,
        location: str,
        goal: str,
    ) -> dict[str, Any]:
        """Refine previously generated rules based on user feedback."""
        context = self._build_generation_context(location, goal)

        prompt = self._format_refinement_prompt(context, current_rules, refinement)

        try:
            raw = self._ollama.generate(
                prompt, system=RULE_REFINEMENT_SYSTEM_PROMPT, format="json"
            )
            result = self._parse_and_validate(raw, context)
            return result
        except Exception as e:
            logger.error(f"Rule refinement failed: {e}")
            return {
                "rules": [],
                "explanation": "",
                "error": f"Rule refinement failed: {e}",
            }

    def _build_generation_context(self, location: str, goal: str) -> dict[str, Any]:
        """Gather all context needed for rule generation."""
        devices = [
            d for d in self._sqlite.get_all_devices()
            if d.location.lower() == location.lower()
        ]

        # Collect all sensor and actuator IDs across devices
        sensor_ids: set[str] = set()
        actuator_ids: set[str] = set()
        device_sections: list[str] = []

        for device in devices:
            for s in device.capabilities.sensors:
                sensor_ids.add(s.id)
            for a in device.capabilities.actuators:
                actuator_ids.add(a.id)

            # Build device description
            status = "online" if device.online else "offline"
            sensors_str = ", ".join(
                f"{s.id} ({s.type}{f' — {s.name}' if s.name else ''})"
                for s in device.capabilities.sensors
            ) or "(none)"
            actuators_str = ", ".join(
                f"{a.id} ({a.type}{f' — {a.name}' if a.name else ''})"
                for a in device.capabilities.actuators
            ) or "(none)"

            # Current readings
            reading_str = "No data yet"
            if self._ws:
                reading = self._ws.get_latest_by_device(device.id)
                if reading:
                    sensor_map = {s.id: s for s in device.capabilities.sensors}
                    parts = []
                    for key, value in reading.items():
                        if key in _METADATA_KEYS or not isinstance(value, (int, float)):
                            continue
                        unit = _resolve_reading_unit(key, sensor_map)
                        parts.append(f"{key}={value:.1f}{unit}")
                    if parts:
                        reading_str = ", ".join(parts)

            device_sections.append(
                f"  {device.id} ({device.name or device.id}) [{status}]\n"
                f"    Sensors: {sensors_str}\n"
                f"    Actuators: {actuators_str}\n"
                f"    Current readings: {reading_str}"
            )

        # Collect baselines for devices at this location
        baselines: list[dict] = []
        for device in devices:
            device_baselines = self._memory.get_all_baselines(device.id)
            for b in device_baselines:
                if b["sampleCount"] >= 10:
                    baselines.append(b)

        # Summarize baselines by sensor (aggregate across hours)
        baseline_summary: dict[str, dict[str, Any]] = {}
        for b in baselines:
            sensor = b["metric"]
            if sensor not in baseline_summary:
                baseline_summary[sensor] = {
                    "avg_values": [],
                    "std_values": [],
                    "total_samples": 0,
                }
            baseline_summary[sensor]["avg_values"].append(b["avg"])
            baseline_summary[sensor]["std_values"].append(b["stdDev"])
            baseline_summary[sensor]["total_samples"] += b["sampleCount"]

        return {
            "location": location,
            "goal": goal,
            "devices": devices,
            "device_sections": device_sections,
            "sensor_ids": sensor_ids,
            "actuators": actuator_ids,
            "baselines": baselines,
            "baseline_summary": baseline_summary,
        }

    def _format_generation_prompt(self, context: dict[str, Any]) -> str:
        """Format the generation prompt with device context."""
        lines = [
            f"Location: {context['location']}",
            f"Goal: {context['goal']}",
            "",
            "Devices at this location:",
        ]
        lines.extend(context["device_sections"])

        # Add baseline summary if available
        if context["baseline_summary"]:
            lines.append("")
            lines.append("Learned baselines (historical averages):")
            for sensor, data in context["baseline_summary"].items():
                avg_of_avgs = sum(data["avg_values"]) / len(data["avg_values"])
                avg_std = sum(data["std_values"]) / len(data["std_values"])
                unit = sensor_unit(sensor)
                lines.append(
                    f"  {sensor}: avg={avg_of_avgs:.1f}{unit} (±{avg_std:.1f}), "
                    f"{data['total_samples']} total samples"
                )

        lines.append("")
        lines.append("Generate rules appropriate for this goal and these capabilities.")

        return "\n".join(lines)

    def _format_refinement_prompt(
        self,
        context: dict[str, Any],
        current_rules: list[dict],
        refinement: str,
    ) -> str:
        """Format the refinement prompt with current rules and requested changes."""
        lines = [
            f"Location: {context['location']}",
            f"Goal: {context['goal']}",
            "",
            "Devices at this location:",
        ]
        lines.extend(context["device_sections"])

        lines.append("")
        lines.append("Current proposed rules:")
        lines.append(json.dumps(current_rules, indent=2))

        lines.append("")
        lines.append(f"User requested changes: {refinement}")
        lines.append("")
        lines.append("Apply the changes and return the full updated rule set.")

        return "\n".join(lines)

    def _parse_and_validate(
        self, raw: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Parse LLM JSON output and validate each rule."""
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse rule generation response: {e}")
            return {
                "rules": [],
                "explanation": "",
                "error": "LLM returned invalid JSON",
            }

        raw_rules = parsed.get("rules", [])
        explanation = parsed.get("explanation", "")
        valid_rules: list[dict] = []
        drop_reasons: list[str] = []

        for rule in raw_rules:
            error = self._validate_generated_rule(rule, context)
            if error:
                logger.warning(f"Dropping invalid generated rule: {error}")
                drop_reasons.append(error)
                continue
            # Clean up: remove null optional fields
            cleaned = self._clean_rule(rule)
            valid_rules.append(cleaned)

        # All rules dropped by validation — report as error with context
        if raw_rules and not valid_rules:
            sensor_list = ", ".join(sorted(context["sensor_ids"])) or "(none)"
            actuator_list = ", ".join(sorted(context["actuators"])) or "(none)"
            return {
                "rules": [],
                "explanation": "",
                "error": (
                    f"The LLM generated {len(raw_rules)} rule(s) but all were "
                    f"invalid (referenced sensors or actuators that don't exist "
                    f"at this location).\n"
                    f"Available sensors: {sensor_list}\n"
                    f"Available actuators: {actuator_list}\n"
                    f"Try describing your setup more specifically, or ask me "
                    f"what devices are at this location."
                ),
            }

        return {
            "rules": valid_rules,
            "explanation": explanation,
            "error": None,
        }

    def _validate_generated_rule(
        self, rule: dict, context: dict[str, Any]
    ) -> str | None:
        """Validate a single generated rule. Returns error message or None."""
        if not isinstance(rule, dict):
            return "Rule is not a dict"

        if not rule.get("name"):
            return "Missing rule name"
        if not rule.get("description"):
            return "Missing rule description"

        condition = rule.get("condition", {})
        action = rule.get("action", {})

        if not isinstance(condition, dict) or not isinstance(action, dict):
            return "condition and action must be dicts"

        # Required condition fields
        sensor = condition.get("sensor")
        if not sensor:
            return "Missing condition.sensor"
        if sensor not in context["sensor_ids"]:
            return f"Unknown sensor '{sensor}' — not on any device at this location"

        operator = condition.get("operator")
        if operator not in VALID_OPERATORS:
            return f"Invalid operator '{operator}'"

        if "threshold" not in condition:
            return "Missing condition.threshold"

        # Required action fields
        target = action.get("target")
        if not target:
            return "Missing action.target"
        if target not in context["actuators"]:
            return f"Unknown actuator '{target}' — not on any device at this location"

        if not action.get("action"):
            return "Missing action.action"
        if not action.get("reason"):
            return "Missing action.reason"

        # Optional field validation
        trend = condition.get("trend")
        if trend and trend not in VALID_TRENDS:
            return f"Invalid trend '{trend}'"

        forecast = condition.get("forecast")
        if forecast and forecast not in VALID_FORECASTS:
            return f"Invalid forecast '{forecast}'"
        if forecast and condition.get("forecast_threshold") is None:
            return "forecast_threshold required when forecast is set"

        return None

    def _clean_rule(self, rule: dict) -> dict:
        """Remove null optional fields from a rule dict for cleanliness."""
        condition = dict(rule.get("condition", {}))
        action = dict(rule.get("action", {}))

        # Remove null optional condition fields
        optional_condition = [
            "trend", "time_of_day", "forecast", "forecast_threshold",
            "forecast_within_minutes", "baseline_deviation",
        ]
        for field in optional_condition:
            if field in condition and condition[field] is None:
                del condition[field]

        # Ensure duration_seconds has a sensible default
        if "duration_seconds" not in condition:
            condition["duration_seconds"] = 30

        return {
            "name": rule["name"],
            "description": rule["description"],
            "condition": condition,
            "action": action,
        }
