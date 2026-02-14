"""Unit tests for decision_engine.py — rules, trend conditions, time-of-day."""

import time
from unittest.mock import patch
from datetime import datetime

from src.services.decision_engine import (
    DecisionEngine,
    Rule,
    RuleCondition,
    RuleAction,
    SensorState,
)
from src.models.telemetry import TelemetryMessage, Reading


def _make_telemetry(temp=22.0, humidity=55.0, device_id="esp32-test"):
    return TelemetryMessage(
        version=1,
        ts=int(time.time() * 1000),
        device_id=device_id,
        location="room1",
        readings=[
            Reading(id="temp1", value=temp, unit="C"),
            Reading(id="hum1", value=humidity, unit="%"),
        ],
    )


def _make_rule(
    name="test_rule",
    sensor="temp1",
    operator=">",
    threshold=25.0,
    duration_seconds=0,
    target="relay1",
    value=True,
    trend=None,
    time_of_day=None,
    forecast=None,
    forecast_threshold=None,
    forecast_within_minutes=15.0,
    baseline_deviation_threshold=None,
):
    return Rule(
        name=name,
        description="test",
        condition=RuleCondition(
            sensor=sensor,
            operator=operator,
            threshold=threshold,
            duration_seconds=duration_seconds,
            trend=trend,
            time_of_day=time_of_day,
            forecast=forecast,
            forecast_threshold=forecast_threshold,
            forecast_within_minutes=forecast_within_minutes,
            baseline_deviation=baseline_deviation_threshold,
        ),
        action=RuleAction(
            target=target,
            action="set",
            value=value,
            reason="test reason",
        ),
    )


class TestThresholdRules:
    def test_above_threshold_triggers(self):
        engine = DecisionEngine(rules=[_make_rule(threshold=25.0)])
        telemetry = _make_telemetry(temp=26.0)
        commands = engine.evaluate(telemetry)
        assert len(commands) == 1
        assert commands[0].target == "relay1"
        assert commands[0].value is True

    def test_below_threshold_no_trigger(self):
        engine = DecisionEngine(rules=[_make_rule(threshold=25.0)])
        telemetry = _make_telemetry(temp=24.0)
        commands = engine.evaluate(telemetry)
        assert len(commands) == 0

    def test_less_than_operator(self):
        engine = DecisionEngine(rules=[_make_rule(operator="<", threshold=20.0)])
        telemetry = _make_telemetry(temp=18.0)
        commands = engine.evaluate(telemetry)
        assert len(commands) == 1

    def test_equal_operator(self):
        engine = DecisionEngine(rules=[_make_rule(operator="==", threshold=22.0)])
        telemetry = _make_telemetry(temp=22.0)
        commands = engine.evaluate(telemetry)
        assert len(commands) == 1

    def test_disabled_rule_skipped(self):
        rule = _make_rule(threshold=25.0)
        rule.enabled = False
        engine = DecisionEngine(rules=[rule])
        telemetry = _make_telemetry(temp=30.0)
        commands = engine.evaluate(telemetry)
        assert len(commands) == 0


class TestDurationRules:
    def test_duration_not_met(self):
        engine = DecisionEngine(rules=[_make_rule(threshold=25.0, duration_seconds=60)])
        telemetry = _make_telemetry(temp=26.0)
        commands = engine.evaluate(telemetry)
        # First evaluation starts the timer but doesn't trigger
        assert len(commands) == 0

    def test_duration_met_after_wait(self):
        rule = _make_rule(threshold=25.0, duration_seconds=0)
        engine = DecisionEngine(rules=[rule])
        telemetry = _make_telemetry(temp=26.0)
        commands = engine.evaluate(telemetry)
        assert len(commands) == 1


class TestCooldown:
    def test_cooldown_prevents_rapid_fire(self):
        engine = DecisionEngine(rules=[_make_rule(threshold=25.0)])
        telemetry = _make_telemetry(temp=26.0)

        # First should trigger
        commands = engine.evaluate(telemetry)
        assert len(commands) == 1

        # Immediate second should be blocked by cooldown
        commands = engine.evaluate(telemetry)
        assert len(commands) == 0


class TestTrendConditions:
    def test_trend_match_triggers(self):
        engine = DecisionEngine(rules=[_make_rule(threshold=22.0, trend="rising")])
        telemetry = _make_telemetry(temp=23.0)
        context = {"trends": {"temp1": {"trend": "rising", "rate": 0.5}}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 1

    def test_trend_mismatch_blocks(self):
        engine = DecisionEngine(rules=[_make_rule(threshold=22.0, trend="rising")])
        telemetry = _make_telemetry(temp=23.0)
        context = {"trends": {"temp1": {"trend": "falling", "rate": -0.5}}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 0

    def test_no_trend_data_blocks(self):
        engine = DecisionEngine(rules=[_make_rule(threshold=22.0, trend="rising")])
        telemetry = _make_telemetry(temp=23.0)
        context = {"trends": {}}  # No trend data for this sensor
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 0

    def test_trend_without_context_blocks(self):
        engine = DecisionEngine(rules=[_make_rule(threshold=22.0, trend="rising")])
        telemetry = _make_telemetry(temp=23.0)
        # No context at all — trend check fails gracefully
        commands = engine.evaluate(telemetry, None)
        assert len(commands) == 0


class TestTimeOfDayConditions:
    @patch("src.services.decision_engine.datetime")
    def test_within_daytime_range(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 2, 14, 14, 30)  # 14:30

        engine = DecisionEngine(
            rules=[_make_rule(threshold=22.0, time_of_day={"after": "08:00", "before": "22:00"})]
        )
        telemetry = _make_telemetry(temp=23.0)
        commands = engine.evaluate(telemetry)
        assert len(commands) == 1

    @patch("src.services.decision_engine.datetime")
    def test_outside_daytime_range(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 2, 14, 3, 0)  # 03:00

        engine = DecisionEngine(
            rules=[_make_rule(threshold=22.0, time_of_day={"after": "08:00", "before": "22:00"})]
        )
        telemetry = _make_telemetry(temp=23.0)
        commands = engine.evaluate(telemetry)
        assert len(commands) == 0

    @patch("src.services.decision_engine.datetime")
    def test_overnight_range(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 2, 14, 23, 30)  # 23:30

        engine = DecisionEngine(
            rules=[_make_rule(threshold=22.0, time_of_day={"after": "22:00", "before": "06:00"})]
        )
        telemetry = _make_telemetry(temp=23.0)
        commands = engine.evaluate(telemetry)
        assert len(commands) == 1


class TestYAMLLoading:
    def test_load_rules_from_yaml(self, tmp_path):
        rules_yaml = tmp_path / "rules.yaml"
        rules_yaml.write_text("""
rules:
  - name: test_high_temp
    description: Turn on cooling
    condition:
      sensor: temp1
      operator: ">"
      threshold: 28
      duration_seconds: 30
      trend: rising
      time_of_day:
        after: "08:00"
        before: "22:00"
    action:
      target: relay1
      action: set
      value: true
      reason: High temperature

llm:
  enabled: true
  escalation_triggers:
    rapid_change: 2.0
""")
        engine = DecisionEngine.from_yaml(rules_yaml)
        assert len(engine.rules) == 1

        rule = engine.rules[0]
        assert rule.name == "test_high_temp"
        assert rule.condition.sensor == "temp1"
        assert rule.condition.threshold == 28
        assert rule.condition.trend == "rising"
        assert rule.condition.time_of_day == {"after": "08:00", "before": "22:00"}
        assert engine.llm_config["enabled"] is True


class TestLLMEscalation:
    def test_rapid_change_triggers_escalation(self):
        engine = DecisionEngine(
            llm_config={"enabled": True, "escalation_triggers": {"rapid_change": 2.0}}
        )

        # First reading establishes baseline
        t1 = _make_telemetry(temp=22.0)
        assert engine.should_escalate_to_llm(t1) is False

        # Large jump should trigger
        t2 = _make_telemetry(temp=25.0)
        result = engine.should_escalate_to_llm(t2)
        assert result is True

    def test_no_escalation_when_disabled(self):
        engine = DecisionEngine(llm_config={"enabled": False})
        t = _make_telemetry(temp=22.0)
        assert engine.should_escalate_to_llm(t) is False


class TestForecastConditions:
    def test_will_exceed_triggers(self):
        """Rising fast enough to breach threshold within horizon."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            forecast="will_exceed",
            forecast_threshold=28.0,
            forecast_within_minutes=15.0,
        )])
        telemetry = _make_telemetry(temp=25.0)
        context = {"forecasts": {"temp1": {"rate": 0.5, "current": 25.0}}}
        commands = engine.evaluate(telemetry, context)
        # (28 - 25) / 0.5 = 6 min, <= 15 → triggers
        assert len(commands) == 1

    def test_will_exceed_too_slow(self):
        """Rising but won't reach threshold in time."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            forecast="will_exceed",
            forecast_threshold=28.0,
            forecast_within_minutes=5.0,
        )])
        telemetry = _make_telemetry(temp=25.0)
        context = {"forecasts": {"temp1": {"rate": 0.1, "current": 25.0}}}
        commands = engine.evaluate(telemetry, context)
        # (28 - 25) / 0.1 = 30 min, > 5 → does not trigger
        assert len(commands) == 0

    def test_will_exceed_falling_away(self):
        """Temperature falling away from threshold."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            forecast="will_exceed",
            forecast_threshold=28.0,
            forecast_within_minutes=15.0,
        )])
        telemetry = _make_telemetry(temp=25.0)
        context = {"forecasts": {"temp1": {"rate": -0.5, "current": 25.0}}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 0

    def test_will_exceed_already_past(self):
        """Already above threshold should still trigger."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            forecast="will_exceed",
            forecast_threshold=28.0,
            forecast_within_minutes=15.0,
        )])
        telemetry = _make_telemetry(temp=30.0)
        context = {"forecasts": {"temp1": {"rate": 0.5, "current": 30.0}}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 1

    def test_will_drop_below_triggers(self):
        """Falling fast enough to drop below threshold within horizon."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            forecast="will_drop_below",
            forecast_threshold=18.0,
            forecast_within_minutes=10.0,
        )])
        telemetry = _make_telemetry(temp=22.0)
        context = {"forecasts": {"temp1": {"rate": -1.0, "current": 22.0}}}
        commands = engine.evaluate(telemetry, context)
        # (22 - 18) / 1.0 = 4 min, <= 10 → triggers
        assert len(commands) == 1

    def test_will_drop_below_rising_away(self):
        """Temperature rising away from low threshold."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            forecast="will_drop_below",
            forecast_threshold=18.0,
            forecast_within_minutes=10.0,
        )])
        telemetry = _make_telemetry(temp=22.0)
        context = {"forecasts": {"temp1": {"rate": 0.5, "current": 22.0}}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 0

    def test_forecast_without_context_blocks(self):
        """No context means forecast cannot be evaluated."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            forecast="will_exceed",
            forecast_threshold=28.0,
        )])
        telemetry = _make_telemetry(temp=25.0)
        commands = engine.evaluate(telemetry, None)
        assert len(commands) == 0

    def test_forecast_no_data_for_sensor_blocks(self):
        """Context exists but no forecast data for this sensor."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            forecast="will_exceed",
            forecast_threshold=28.0,
        )])
        telemetry = _make_telemetry(temp=25.0)
        context = {"forecasts": {}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 0

    def test_forecast_combined_with_trend(self):
        """Forecast + trend must both match."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            forecast="will_exceed",
            forecast_threshold=28.0,
            forecast_within_minutes=15.0,
            trend="rising",
        )])
        telemetry = _make_telemetry(temp=25.0)
        context = {
            "trends": {"temp1": {"trend": "rising", "rate": 0.5}},
            "forecasts": {"temp1": {"rate": 0.5, "current": 25.0}},
        }
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 1

    def test_forecast_combined_with_wrong_trend_blocks(self):
        """Forecast passes but trend doesn't match."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            forecast="will_exceed",
            forecast_threshold=28.0,
            forecast_within_minutes=15.0,
            trend="falling",
        )])
        telemetry = _make_telemetry(temp=25.0)
        context = {
            "trends": {"temp1": {"trend": "rising", "rate": 0.5}},
            "forecasts": {"temp1": {"rate": 0.5, "current": 25.0}},
        }
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 0


class TestBaselineDeviationConditions:
    def test_deviation_exceeds_threshold_triggers(self):
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            baseline_deviation_threshold=2.0,
        )])
        telemetry = _make_telemetry(temp=26.0)
        context = {"baselines": {"temp1": {"avg": 22.0, "std_dev": 1.5, "deviation": 2.67}}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 1

    def test_deviation_below_threshold_blocks(self):
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            baseline_deviation_threshold=2.0,
        )])
        telemetry = _make_telemetry(temp=23.0)
        context = {"baselines": {"temp1": {"avg": 22.0, "std_dev": 1.5, "deviation": 0.67}}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 0

    def test_negative_deviation_uses_absolute(self):
        """Below-baseline deviations should also trigger (abnormally low)."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            baseline_deviation_threshold=2.0,
        )])
        telemetry = _make_telemetry(temp=18.0)
        context = {"baselines": {"temp1": {"avg": 22.0, "std_dev": 1.5, "deviation": -2.67}}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 1

    def test_no_baseline_data_blocks(self):
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            baseline_deviation_threshold=2.0,
        )])
        telemetry = _make_telemetry(temp=26.0)
        context = {"baselines": {}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 0

    def test_no_context_blocks(self):
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            baseline_deviation_threshold=2.0,
        )])
        telemetry = _make_telemetry(temp=26.0)
        commands = engine.evaluate(telemetry, None)
        assert len(commands) == 0

    def test_deviation_none_blocks(self):
        """Baseline exists but deviation is None (not enough samples)."""
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            baseline_deviation_threshold=2.0,
        )])
        telemetry = _make_telemetry(temp=26.0)
        context = {"baselines": {"temp1": {"avg": 22.0, "std_dev": 1.5, "deviation": None}}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 0

    @patch("src.services.decision_engine.datetime")
    def test_baseline_combined_with_time_of_day(self, mock_dt):
        """Baseline + time_of_day (the nighttime anomaly use case)."""
        mock_dt.now.return_value = datetime(2026, 2, 14, 23, 30)
        engine = DecisionEngine(rules=[_make_rule(
            threshold=0, operator=">=",
            baseline_deviation_threshold=2.0,
            time_of_day={"after": "22:00", "before": "06:00"},
        )])
        telemetry = _make_telemetry(temp=26.0)
        context = {"baselines": {"temp1": {"deviation": 2.5}}}
        commands = engine.evaluate(telemetry, context)
        assert len(commands) == 1


class TestYAMLForecastLoading:
    def test_load_forecast_rules_from_yaml(self, tmp_path):
        rules_yaml = tmp_path / "rules.yaml"
        rules_yaml.write_text("""
rules:
  - name: preemptive_cooling
    description: Start fan before threshold
    condition:
      sensor: temp1
      forecast: will_exceed
      forecast_threshold: 28
      forecast_within_minutes: 15
    action:
      target: relay1
      action: set
      value: true
      reason: Predicted temp will exceed 28C

  - name: abnormal_nighttime_heat
    description: Abnormal nighttime temperature
    condition:
      sensor: temp1
      baseline_deviation: 2.0
      time_of_day:
        after: "22:00"
        before: "06:00"
    action:
      target: relay1
      action: set
      value: true
      reason: Temperature abnormally high for nighttime
""")
        engine = DecisionEngine.from_yaml(rules_yaml)
        assert len(engine.rules) == 2

        r1 = engine.rules[0]
        assert r1.condition.forecast == "will_exceed"
        assert r1.condition.forecast_threshold == 28
        assert r1.condition.forecast_within_minutes == 15

        r2 = engine.rules[1]
        assert r2.condition.baseline_deviation == 2.0
        assert r2.condition.time_of_day == {"after": "22:00", "before": "06:00"}
