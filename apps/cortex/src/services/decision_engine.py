import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from pathlib import Path

import yaml

from ..models.telemetry import TelemetryMessage
from ..models.command import Command
from ..config import DEFAULT_DEVICE_ID, DEFAULT_LOCATION

logger = logging.getLogger(__name__)


@dataclass
class RuleCondition:
    """A condition that must be met for a rule to trigger."""
    sensor: str
    operator: str  # >, <, >=, <=, ==, !=
    threshold: float | str
    duration_seconds: int = 0
    # Phase 1: trend and time-of-day conditions
    trend: str | None = None            # "rising" | "falling" | "stable"
    trend_window_minutes: int = 30
    time_of_day: dict | None = None     # {"after": "08:00", "before": "22:00"}


@dataclass
class RuleAction:
    """An action to take when a rule triggers."""
    target: str
    action: str
    value: Any
    reason: str


@dataclass
class Rule:
    """A rule that maps conditions to actions."""
    name: str
    description: str
    condition: RuleCondition
    action: RuleAction
    enabled: bool = True


@dataclass
class SensorState:
    """Track state for a sensor to handle duration-based rules."""
    last_value: float | str | None = None
    condition_met_since: float | None = None
    last_action_time: float = 0
    cooldown_seconds: float = 60  # Prevent rapid toggling


@dataclass
class DecisionEngine:
    """Rule-based decision engine for sensor data."""
    rules: list[Rule] = field(default_factory=list)
    sensor_states: dict[str, SensorState] = field(default_factory=dict)
    llm_config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DecisionEngine":
        """Load rules from a YAML config file."""
        with open(path) as f:
            config = yaml.safe_load(f)

        rules = []
        for rule_data in config.get("rules", []):
            condition_data = rule_data.get("condition", {})
            action_data = rule_data.get("action", {})

            rule = Rule(
                name=rule_data.get("name", ""),
                description=rule_data.get("description", ""),
                condition=RuleCondition(
                    sensor=condition_data.get("sensor", ""),
                    operator=condition_data.get("operator", ">"),
                    threshold=condition_data.get("threshold", 0),
                    duration_seconds=condition_data.get("duration_seconds", 0),
                    trend=condition_data.get("trend"),
                    trend_window_minutes=condition_data.get("trend_window_minutes", 30),
                    time_of_day=condition_data.get("time_of_day"),
                ),
                action=RuleAction(
                    target=action_data.get("target", ""),
                    action=action_data.get("action", "set"),
                    value=action_data.get("value"),
                    reason=action_data.get("reason", ""),
                ),
                enabled=rule_data.get("enabled", True),
            )
            rules.append(rule)
            logger.info(f"Loaded rule: {rule.name}")

        llm_config = config.get("llm", {})

        return cls(rules=rules, llm_config=llm_config)

    def evaluate(
        self,
        telemetry: TelemetryMessage,
        context: dict[str, Any] | None = None,
    ) -> list[Command]:
        """Evaluate rules against telemetry data. Returns commands to execute."""
        commands = []
        now = time.time()

        for rule in self.rules:
            if not rule.enabled:
                continue

            # Get sensor reading
            reading = telemetry.get_reading(rule.condition.sensor)
            if reading is None:
                continue

            # Get or create sensor state
            state_key = f"{telemetry.device_id}:{rule.condition.sensor}:{rule.name}"
            if state_key not in self.sensor_states:
                self.sensor_states[state_key] = SensorState()
            state = self.sensor_states[state_key]

            # Check if threshold condition is met
            condition_met = self._check_condition(reading.value, rule.condition)

            # Check trend condition (Phase 1)
            if condition_met and rule.condition.trend:
                if not context:
                    condition_met = False  # Trend required but no context available
                else:
                    trend_data = context.get("trends", {}).get(rule.condition.sensor)
                    if trend_data:
                        condition_met = trend_data.get("trend") == rule.condition.trend
                    else:
                        condition_met = False  # No trend data available

            # Check time-of-day condition (Phase 1)
            if condition_met and rule.condition.time_of_day:
                condition_met = self._check_time_of_day(rule.condition.time_of_day)

            if condition_met:
                # Track when condition started being met
                if state.condition_met_since is None:
                    state.condition_met_since = now
                    logger.debug(f"Rule {rule.name}: condition started")

                # Check if duration requirement is met
                duration_met = (now - state.condition_met_since) >= rule.condition.duration_seconds

                # Check cooldown
                cooldown_ok = (now - state.last_action_time) >= state.cooldown_seconds

                if duration_met and cooldown_ok:
                    command = Command(
                        device_id=telemetry.device_id,
                        location=telemetry.location,
                        target=rule.action.target,
                        action=rule.action.action,
                        value=rule.action.value,
                        reason=rule.action.reason,
                    )
                    commands.append(command)
                    state.last_action_time = now
                    logger.info(f"Rule {rule.name} triggered: {rule.action.reason}")
            else:
                # Reset condition tracking
                if state.condition_met_since is not None:
                    logger.debug(f"Rule {rule.name}: condition no longer met")
                state.condition_met_since = None

            state.last_value = reading.value

        return commands

    def _check_condition(self, value: float | str, condition: RuleCondition) -> bool:
        """Check if a value meets a condition."""
        try:
            # Handle numeric comparisons
            if isinstance(value, (int, float)) and isinstance(condition.threshold, (int, float)):
                if condition.operator == ">":
                    return value > condition.threshold
                elif condition.operator == "<":
                    return value < condition.threshold
                elif condition.operator == ">=":
                    return value >= condition.threshold
                elif condition.operator == "<=":
                    return value <= condition.threshold
                elif condition.operator == "==":
                    return value == condition.threshold
                elif condition.operator == "!=":
                    return value != condition.threshold

            # Handle string comparisons
            if condition.operator == "==":
                return str(value) == str(condition.threshold)
            elif condition.operator == "!=":
                return str(value) != str(condition.threshold)

        except (TypeError, ValueError) as e:
            logger.error(f"Error evaluating condition: {e}")

        return False

    def _check_time_of_day(self, time_range: dict) -> bool:
        """Check if current time is within the specified range."""
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute

        after_str = time_range.get("after", "00:00")
        before_str = time_range.get("before", "23:59")

        after_parts = after_str.split(":")
        before_parts = before_str.split(":")
        after_minutes = int(after_parts[0]) * 60 + int(after_parts[1])
        before_minutes = int(before_parts[0]) * 60 + int(before_parts[1])

        if after_minutes <= before_minutes:
            return after_minutes <= current_minutes <= before_minutes
        else:
            # Overnight range (e.g., 22:00-06:00)
            return current_minutes >= after_minutes or current_minutes <= before_minutes

    def should_escalate_to_llm(self, telemetry: TelemetryMessage) -> bool:
        """Check if the situation should be escalated to the LLM."""
        if not self.llm_config.get("enabled", False):
            return False

        triggers = self.llm_config.get("escalation_triggers", {})

        # Check for rapid temperature change
        if "rapid_change" in triggers:
            state_key = f"{telemetry.device_id}:temp1:_rapid"
            if state_key not in self.sensor_states:
                self.sensor_states[state_key] = SensorState()
            state = self.sensor_states[state_key]

            reading = telemetry.get_reading("temp1")
            if reading and state.last_value is not None:
                change = abs(float(reading.value) - float(state.last_value))
                # Assuming telemetry every 5 seconds, scale to per-minute
                change_per_minute = change * 12
                if change_per_minute > triggers["rapid_change"]:
                    logger.info(f"LLM escalation: rapid temp change {change_per_minute:.1f}°C/min")
                    return True
            if reading:
                state.last_value = reading.value

        return False

    def get_context_summary(self) -> dict[str, Any]:
        """Get a summary of current sensor states for LLM context."""
        summary = {}
        for key, state in self.sensor_states.items():
            parts = key.split(":")
            if len(parts) >= 2:
                device_id, sensor = parts[0], parts[1]
                if device_id not in summary:
                    summary[device_id] = {}
                summary[device_id][sensor] = {
                    "last_value": state.last_value,
                    "condition_active": state.condition_met_since is not None,
                }
        return summary
