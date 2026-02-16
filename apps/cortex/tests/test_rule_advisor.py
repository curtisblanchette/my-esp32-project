"""Tests for the Rule Advisor service."""

import json
import time
from unittest.mock import MagicMock

import pytest

from src.services.rule_advisor import (
    RuleAdvisor, AUTO_APPLY_CONFIDENCE,
    EFFECTIVENESS_GUARD_THRESHOLD, EFFECTIVENESS_GUARD_MIN_SAMPLES,
    TOO_SENSITIVE_MAX_CHANGE_PCT,
)
from src.services.decision_engine import DecisionEngine, Rule, RuleCondition, RuleAction
from src.services.cortex_memory import CortexMemory
from src.services.outcome_tracker import OutcomeTracker


def _make_engine(rules=None):
    """Create a DecisionEngine with test rules."""
    engine = DecisionEngine()
    engine.rules = rules or [
        Rule(
            name="high_temp_alert",
            description="Turn on fan when temp > 25",
            condition=RuleCondition(sensor="temp1", operator=">", threshold=25, duration_seconds=15),
            action=RuleAction(target="relay1", action="set", value=True, reason="Temperature exceeded 25°C"),
        ),
        Rule(
            name="low_temp_restore",
            description="Turn off fan when temp < 18",
            condition=RuleCondition(sensor="temp1", operator="<", threshold=18, duration_seconds=10),
            action=RuleAction(target="relay1", action="set", value=False, reason="Temperature dropped below 18°C"),
        ),
    ]
    return engine


def _make_ollama(response_json=None):
    """Create a mock OllamaClient."""
    mock = MagicMock()
    mock.is_available.return_value = True
    if response_json is not None:
        mock.generate.return_value = json.dumps(response_json)
    else:
        mock.generate.return_value = json.dumps({"suggestions": []})
    return mock


def _seed_outcomes(sqlite_db, count=10, reason="Temperature exceeded 25°C", effectiveness=0.5):
    """Seed the cortex_outcomes table with test data."""
    db = sqlite_db._get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS cortex_outcomes (
            correlation_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            target TEXT NOT NULL,
            action TEXT NOT NULL,
            value JSON,
            reason TEXT,
            command_ts INTEGER NOT NULL,
            ack_status TEXT NOT NULL,
            target_metric TEXT NOT NULL,
            desired_direction TEXT NOT NULL,
            pre_value REAL NOT NULL,
            post_1m REAL,
            post_5m REAL,
            post_10m REAL,
            effectiveness REAL NOT NULL,
            scored_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_outcomes_device_target
            ON cortex_outcomes(device_id, target);
        CREATE INDEX IF NOT EXISTS idx_outcomes_ts
            ON cortex_outcomes(command_ts);
    """)
    db.commit()

    now = int(time.time() * 1000)
    for i in range(count):
        db.execute(
            "INSERT INTO cortex_outcomes "
            "(correlation_id, device_id, target, action, value, reason, command_ts, "
            "ack_status, target_metric, desired_direction, pre_value, post_1m, post_5m, "
            "post_10m, effectiveness, scored_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"corr-{i}", "esp32-test", "relay1", "set",
                json.dumps(True), reason, now - (i * 60000),
                "executed", "temperature", "decrease",
                26.0, 25.5, 24.8, 24.0, effectiveness, now,
            ),
        )
    db.commit()


def _seed_device(sqlite_db, device_id="esp32-test"):
    """Seed a test device."""
    sqlite_db.upsert_device(
        id=device_id,
        location="room1",
        capabilities={"sensors": [{"id": "temp1", "type": "temperature"}], "actuators": []},
    )


class TestRuleAdvisor:

    def test_analyze_with_no_outcomes_and_no_baselines(self, sqlite_db):
        """Returns empty list when no outcome data and no baselines exist."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        mock_ollama = _make_ollama()
        # Need outcome tracker with the outcomes table
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()

        assert result == []
        mock_ollama.generate.assert_not_called()

    def test_analyze_produces_suggestions(self, sqlite_db):
        """LLM suggestions are stored in the database."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=10, effectiveness=0.3)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        mock_ollama = _make_ollama({
            "suggestions": [{
                "rule_name": "high_temp_alert",
                "field": "threshold",
                "suggested_value": 27,
                "reason": "Threshold too sensitive based on outcome data",
                "confidence": 0.7,
            }]
        })

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()

        assert len(result) == 1
        assert result[0]["ruleName"] == "high_temp_alert"
        assert result[0]["field"] == "threshold"
        assert result[0]["status"] == "pending"  # Below auto-apply threshold
        assert result[0]["confidence"] == 0.7

        # Verify stored in DB
        stored = sqlite_db.get_suggestions()
        assert len(stored) == 1

    def test_auto_apply_high_confidence_threshold(self, sqlite_db):
        """High-confidence threshold suggestions are auto-applied."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=10, effectiveness=0.3)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        mock_ollama = _make_ollama({
            "suggestions": [{
                "rule_name": "high_temp_alert",
                "field": "threshold",
                "suggested_value": 27,
                "reason": "Outcomes show poor effectiveness at 25",
                "confidence": 0.85,
            }]
        })

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()

        assert len(result) == 1
        assert result[0]["status"] == "applied"

        # Verify rule was modified in-memory
        assert engine.rules[0].condition.threshold == 27

    def test_no_auto_apply_low_confidence(self, sqlite_db):
        """Low-confidence suggestions stay pending."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=10, effectiveness=0.3)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        mock_ollama = _make_ollama({
            "suggestions": [{
                "rule_name": "high_temp_alert",
                "field": "threshold",
                "suggested_value": 27,
                "reason": "Might help",
                "confidence": 0.5,
            }]
        })

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()

        assert len(result) == 1
        assert result[0]["status"] == "pending"
        # Rule should NOT be modified
        assert engine.rules[0].condition.threshold == 25

    def test_no_auto_apply_non_threshold_field(self, sqlite_db):
        """High confidence but non-threshold field stays pending."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=10, effectiveness=0.3)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        mock_ollama = _make_ollama({
            "suggestions": [{
                "rule_name": "high_temp_alert",
                "field": "duration_seconds",
                "suggested_value": 30,
                "reason": "Longer duration avoids false triggers",
                "confidence": 0.9,
            }]
        })

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()

        assert len(result) == 1
        assert result[0]["status"] == "pending"
        # Rule should NOT be modified
        assert engine.rules[0].condition.duration_seconds == 15

    def test_apply_suggestion_manually(self, sqlite_db):
        """Manual approval applies the suggestion and modifies the rule."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=10, effectiveness=0.3)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        mock_ollama = _make_ollama({
            "suggestions": [{
                "rule_name": "high_temp_alert",
                "field": "duration_seconds",
                "suggested_value": 30,
                "reason": "Longer duration",
                "confidence": 0.6,
            }]
        })

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()
        assert result[0]["status"] == "pending"

        # Manually approve
        ok = advisor.apply_suggestion(result[0]["id"])
        assert ok is True
        assert engine.rules[0].condition.duration_seconds == 30

        # Verify status updated
        stored = sqlite_db.get_suggestion(result[0]["id"])
        assert stored["status"] == "applied"

    def test_reject_suggestion(self, sqlite_db):
        """Rejection updates status without modifying rules."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=10, effectiveness=0.3)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        mock_ollama = _make_ollama({
            "suggestions": [{
                "rule_name": "high_temp_alert",
                "field": "threshold",
                "suggested_value": 30,
                "reason": "Too aggressive",
                "confidence": 0.6,
            }]
        })

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()

        ok = advisor.reject_suggestion(result[0]["id"])
        assert ok is True

        # Rule unchanged
        assert engine.rules[0].condition.threshold == 25

        # Status is rejected
        stored = sqlite_db.get_suggestion(result[0]["id"])
        assert stored["status"] == "rejected"

    def test_collect_rule_performance(self, sqlite_db):
        """Rule performance data aggregation."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_outcomes(sqlite_db, count=5, reason="Temperature exceeded 25°C", effectiveness=0.6)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        perf = advisor._collect_rule_performance()

        assert len(perf) == 2  # Both rules
        high_temp = next(p for p in perf if p["rule_name"] == "high_temp_alert")
        assert high_temp["sample_count"] == 5
        assert high_temp["avg_effectiveness"] == 0.6
        assert high_temp["success_rate"] == 1.0  # All > 0.1

    def test_collect_observation_summary(self, sqlite_db):
        """Observation data grouped by category."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())
        _seed_outcomes(sqlite_db, count=0)

        # Seed observations
        now = int(time.time() * 1000)
        sqlite_db.insert_event(now - 1000, "esp32-test", "observation", {"category": "mold"}, "human")
        sqlite_db.insert_event(now - 2000, "esp32-test", "observation", {"category": "mold"}, "human")
        sqlite_db.insert_event(now - 3000, "esp32-test", "observation", {"category": "needs_water"}, "human")

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        summary = advisor._collect_observation_summary()

        assert summary is not None
        assert "mold: 2 occurrence(s)" in summary
        assert "needs_water: 1 occurrence(s)" in summary

    def test_parse_suggestions_malformed_json(self, sqlite_db):
        """Graceful handling of bad LLM output."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=10, effectiveness=0.3)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        mock_ollama = MagicMock()
        mock_ollama.is_available.return_value = True
        mock_ollama.generate.return_value = "this is not valid json at all"

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()

        assert result == []

    def test_parse_suggestions_unknown_rule_skipped(self, sqlite_db):
        """Suggestions for unknown rules are silently skipped."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=10, effectiveness=0.3)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        mock_ollama = _make_ollama({
            "suggestions": [{
                "rule_name": "nonexistent_rule",
                "field": "threshold",
                "suggested_value": 30,
                "reason": "Ghost rule",
                "confidence": 0.9,
            }]
        })

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()

        assert result == []

    def test_ollama_unavailable(self, sqlite_db):
        """Returns empty when Ollama is unavailable."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=10, effectiveness=0.3)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        mock_ollama = MagicMock()
        mock_ollama.is_available.return_value = False

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()

        assert result == []

    def test_last_run_ts_updated(self, sqlite_db):
        """last_run_ts is updated after analyze()."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        assert advisor.last_run_ts is None

        advisor.analyze()
        assert advisor.last_run_ts is not None
        assert advisor.last_run_ts > 0


def _seed_baselines(memory, device_id="esp32-test", metric="temperature",
                    hours=range(24), avg=22.0, std_dev=1.0, count_per_hour=50):
    """Seed baselines via CortexMemory for a device/metric across hours.

    Uses update_baseline repeatedly to build up the desired stats.
    For simplicity, we write directly to SQLite instead.
    """
    import math
    db = memory._sqlite._get_db()
    now = int(time.time() * 1000)
    for h in hours:
        sum_v = avg * count_per_hour
        sum_sq = (std_dev ** 2 + avg ** 2) * count_per_hour
        actual_var = (sum_sq / count_per_hour) - (avg ** 2)
        actual_std = math.sqrt(max(0, actual_var))
        db.execute(
            """INSERT OR REPLACE INTO cortex_baselines
               (device_id, sensor, hour_of_day, avg_value, std_dev,
                sample_count, sum_values, sum_squares, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (device_id, metric, h, avg, actual_std, count_per_hour,
             sum_v, sum_sq, now),
        )
    db.commit()


class TestDeterministicGapAnalysis:

    def test_unreachable_threshold(self, sqlite_db):
        """Rule at 35°C, baselines max avg+2σ = 24°C → suggests lowering."""
        engine = _make_engine([
            Rule(
                name="hot_rule",
                description="Fan on when very hot",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=35, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Too hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=22, std=1 → max upper bound = 22 + 2*1 = 24
        _seed_baselines(memory, avg=22.0, std_dev=1.0)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        assert len(suggestions) >= 1
        unreachable = next(s for s in suggestions if s["field"] == "threshold")
        assert unreachable["rule_name"] == "hot_rule"
        assert unreachable["suggested_value"] == 24.0
        assert "unreachable" in unreachable["reason"].lower()
        assert 0 < unreachable["confidence"] <= 1.0

    def test_unreachable_threshold_less_than(self, sqlite_db):
        """Rule < 10, baselines min avg-2σ = 15°C → suggests raising."""
        engine = _make_engine([
            Rule(
                name="cold_rule",
                description="Alert when too cold",
                condition=RuleCondition(sensor="temp1", operator="<", threshold=10, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Too cold"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=20, std=2.5 → min lower bound = 20 - 2*2.5 = 15
        _seed_baselines(memory, avg=20.0, std_dev=2.5)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        assert len(suggestions) >= 1
        unreachable = next(s for s in suggestions if s["field"] == "threshold")
        assert unreachable["rule_name"] == "cold_rule"
        assert unreachable["suggested_value"] == 15.0
        assert "unreachable" in unreachable["reason"].lower()

    def test_too_sensitive(self, sqlite_db):
        """Rule at 21°C, baselines avg ~21±1°C → suggests moving to ~23°C."""
        engine = _make_engine([
            Rule(
                name="sensitive_rule",
                description="Fan on when warm",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=21, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Warm"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=21, std=1 → threshold 21 ≤ avg + 1σ = 22 for ALL hours
        _seed_baselines(memory, avg=21.0, std_dev=1.0)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        sensitive = [s for s in suggestions if "fires too often" in s.get("reason", "")]
        assert len(sensitive) == 1
        # Suggested value should be avg + 2σ = 23.0
        assert sensitive[0]["suggested_value"] == 23.0
        assert sensitive[0]["confidence"] > 0.5

    def test_suggests_baseline_deviation(self, sqlite_db):
        """Stable baselines (CV < 10%), no baseline_deviation → suggests 2.0."""
        engine = _make_engine([
            Rule(
                name="stable_rule",
                description="Fan when hot",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=30, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=22, std=1 → CV = 1/22 ≈ 4.5% < 10%, 24 hours covered
        _seed_baselines(memory, avg=22.0, std_dev=1.0)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        bd = [s for s in suggestions if s["field"] == "baseline_deviation"]
        assert len(bd) == 1
        assert bd[0]["suggested_value"] == 2.0
        assert bd[0]["confidence"] == 0.65

    def test_no_suggestion_when_baseline_deviation_exists(self, sqlite_db):
        """Rule already has baseline_deviation → no duplicate suggestion."""
        engine = _make_engine([
            Rule(
                name="already_has_bd",
                description="Has baseline deviation",
                condition=RuleCondition(
                    sensor="temp1", operator=">", threshold=25,
                    duration_seconds=10, baseline_deviation=2.0,
                ),
                action=RuleAction(target="relay1", action="set", value=True, reason="Hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        _seed_baselines(memory, avg=22.0, std_dev=1.0)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        bd = [s for s in suggestions if s["field"] == "baseline_deviation"]
        assert len(bd) == 0

    def test_stale_forecast_threshold(self, sqlite_db):
        """Forecast threshold unreachable → suggests adjustment."""
        engine = _make_engine([
            Rule(
                name="stale_forecast",
                description="Preemptive cooling",
                condition=RuleCondition(
                    sensor="temp1", operator=">=", threshold=0,
                    forecast="will_exceed", forecast_threshold=40.0,
                    forecast_within_minutes=15,
                ),
                action=RuleAction(target="relay1", action="set", value=True, reason="Forecast hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=22, std=1 → max upper = 24, so forecast_threshold=40 is unreachable
        _seed_baselines(memory, avg=22.0, std_dev=1.0)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        fc = [s for s in suggestions if s["field"] == "forecast_threshold"]
        assert len(fc) == 1
        assert fc[0]["suggested_value"] == 24.0
        assert "unreachable" in fc[0]["reason"].lower()

    def test_skips_cross_device_rules(self, sqlite_db):
        """Rule with scope='any' → skipped."""
        engine = _make_engine([
            Rule(
                name="cross_device",
                description="Cross device rule",
                condition=RuleCondition(
                    sensor="temp1", operator=">", threshold=50,
                    duration_seconds=10, scope="any",
                ),
                action=RuleAction(target="relay1", action="set", value=True, reason="Hot everywhere"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        _seed_baselines(memory, avg=22.0, std_dev=1.0)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        assert suggestions == []

    def test_skips_insufficient_baselines(self, sqlite_db):
        """< 10 samples total → no suggestions."""
        engine = _make_engine([
            Rule(
                name="low_samples",
                description="Rule with few baseline samples",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=50, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # Only 3 samples per hour across 2 hours = 6 total < 10
        _seed_baselines(memory, avg=22.0, std_dev=1.0, hours=range(2), count_per_hour=3)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        assert suggestions == []

    def test_noop_unreachable_gt_skipped(self, sqlite_db):
        """Threshold already at floor(max_upper) → no suggestion (would be no-op)."""
        # avg=20.335, std=0.005 → max_upper = 20.345 → floor = 20.34
        # If threshold is already 20.34, the suggestion would be a no-op
        engine = _make_engine([
            Rule(
                name="noop_rule",
                description="Already adjusted",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=20.34, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        _seed_baselines(memory, avg=20.335, std_dev=0.005)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        threshold_suggestions = [s for s in suggestions if s["field"] == "threshold" and "unreachable" in s.get("reason", "")]
        assert threshold_suggestions == []

    def test_noop_unreachable_lt_skipped(self, sqlite_db):
        """Threshold already at ceil(min_lower) → no suggestion (would be no-op)."""
        # avg=20.335, std=0.005 → min_lower = 20.325 → ceil = 20.33
        # If threshold is already 20.33, the suggestion would be a no-op
        engine = _make_engine([
            Rule(
                name="noop_lt_rule",
                description="Already adjusted",
                condition=RuleCondition(sensor="temp1", operator="<", threshold=20.33, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Cold"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        _seed_baselines(memory, avg=20.335, std_dev=0.005)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        threshold_suggestions = [s for s in suggestions if s["field"] == "threshold" and "unreachable" in s.get("reason", "")]
        assert threshold_suggestions == []

    def test_unreachable_lt_rounds_up(self, sqlite_db):
        """For < operator, suggested value rounds UP so it's actually reachable."""
        # avg=20.0, std=2.5 → min_lower = 15.0
        # threshold=10 < 15 → unreachable, suggested = ceil(15.0) = 15.0
        engine = _make_engine([
            Rule(
                name="cold_round_rule",
                description="Alert when cold",
                condition=RuleCondition(sensor="temp1", operator="<", threshold=10, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Cold"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # get_all_baselines rounds avg to 2dp, stdDev to 3dp
        # avg=20.0, std=2.497 → min_lower = 20.0 - 4.994 = 15.006 → ceil(1500.6)/100 = 15.01
        _seed_baselines(memory, avg=20.0, std_dev=2.497)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        unreachable = [s for s in suggestions if s["field"] == "threshold" and "unreachable" in s.get("reason", "")]
        assert len(unreachable) == 1
        assert unreachable[0]["suggested_value"] == 15.01

    def test_unreachable_gt_rounds_down(self, sqlite_db):
        """For > operator, suggested value rounds DOWN so it's actually reachable."""
        engine = _make_engine([
            Rule(
                name="hot_round_rule",
                description="Fan when hot",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=30, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=22.0, std=1.003 → max_upper = 24.006 → floor = 24.00
        _seed_baselines(memory, avg=22.0, std_dev=1.003)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        unreachable = [s for s in suggestions if s["field"] == "threshold" and "unreachable" in s.get("reason", "")]
        assert len(unreachable) == 1
        assert unreachable[0]["suggested_value"] == 24.0

    def test_analyze_skips_noop_suggestions(self, sqlite_db):
        """Full analyze() pipeline skips suggestions where suggested == current."""
        engine = _make_engine([
            Rule(
                name="noop_full",
                description="Already at boundary",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=24.0, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=22, std=1 → max_upper = 24.0 → floor = 24.0 == threshold → skipped by detector
        # Even if it somehow got through, analyze() has a second no-op guard
        _seed_baselines(memory, avg=22.0, std_dev=1.0)

        mock_ollama = MagicMock()
        mock_ollama.is_available.return_value = False

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()

        # No stored suggestions — the threshold is already at the boundary
        threshold_results = [r for r in result if r["field"] == "threshold"]
        assert threshold_results == []

    def test_near_boundary_threshold_not_retriggered(self, sqlite_db):
        """Threshold just below the boundary (within epsilon) should NOT re-trigger unreachable.

        Reproduces the real bug: rule_advisor applied 20.67, then min_lower=20.674,
        and 20.67 < 20.674 re-triggered unreachable even though it was just adjusted.
        """
        engine = _make_engine([
            Rule(
                name="low_temp_restore",
                description="Restore when cold",
                # Threshold was previously adjusted to 20.67
                condition=RuleCondition(sensor="temp1", operator="<", threshold=20.67, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=False, reason="Cold"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=21.03, std=0.178 → min_lower = 21.03 - 0.356 = 20.674
        # threshold=20.67 is barely below min_lower (gap=0.004), within epsilon
        _seed_baselines(memory, avg=21.03, std_dev=0.178)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        unreachable = [s for s in suggestions if s["field"] == "threshold" and "unreachable" in s.get("reason", "")]
        assert unreachable == [], f"Should not re-trigger on near-boundary threshold, got: {unreachable}"

    def test_returns_empty_when_well_calibrated(self, sqlite_db):
        """Threshold properly outside normal range → no suggestions."""
        engine = _make_engine([
            Rule(
                name="well_calibrated",
                description="Good threshold",
                # threshold=26 > avg+2σ=24 but not unreachable (it IS beyond 2σ for > operator)
                # Actually: avg=22, std=1 → avg+2σ=24, threshold=26 > 24 → unreachable!
                # For "well calibrated": threshold=23.5 → between avg+1σ=23 and avg+2σ=24
                # Not unreachable (23.5 < 24). Not too sensitive (23.5 > avg+1σ=23 for 0% of hours).
                condition=RuleCondition(sensor="temp1", operator=">", threshold=23.5, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Warm"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=22, std=1 → avg+1σ=23, avg+2σ=24
        # threshold=23.5: not unreachable (23.5 < 24), not sensitive (23.5 > 23 for 0% of hours)
        _seed_baselines(memory, avg=22.0, std_dev=1.0)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        suggestions = advisor._deterministic_gap_analysis(advisor._collect_baseline_summary())

        # Filter out baseline_deviation suggestions (separate concern)
        threshold_suggestions = [s for s in suggestions if s["field"] != "baseline_deviation"]
        assert threshold_suggestions == []


class TestEffectivenessGuard:
    """Tests for the effectiveness guard that prevents over-correction of working rules."""

    def test_effective_rule_skips_unreachable(self, sqlite_db):
        """Rule with high effectiveness (>0.5, >=3 samples) is NOT flagged as unreachable."""
        engine = _make_engine([
            Rule(
                name="exhaust_on",
                description="Exhaust when hot",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=28, duration_seconds=60),
                action=RuleAction(target="relay2", action="set", value=True, reason="Too hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # Baselines reflect controlled environment (fan keeps temp at ~25)
        # avg=25, std=0.8 → max_upper = 25 + 2*0.8 = 26.6
        # threshold=28 > 26.6 → would be "unreachable" without guard
        _seed_baselines(memory, avg=25.0, std_dev=0.8)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        baselines = advisor._collect_baseline_summary()

        rule_performance = [{
            "rule_name": "exhaust_on",
            "sample_count": 5,
            "avg_effectiveness": 0.7,
            "success_rate": 0.8,
        }]
        suggestions = advisor._deterministic_gap_analysis(baselines, rule_performance)
        unreachable = [s for s in suggestions if "unreachable" in s.get("reason", "")]
        assert unreachable == [], "Effective rule should NOT be flagged as unreachable"

    def test_ineffective_rule_still_flagged(self, sqlite_db):
        """Rule with low effectiveness IS still flagged as unreachable."""
        engine = _make_engine([
            Rule(
                name="broken_rule",
                description="Broken threshold",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=28, duration_seconds=60),
                action=RuleAction(target="relay2", action="set", value=True, reason="Too hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        _seed_baselines(memory, avg=25.0, std_dev=0.8)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        baselines = advisor._collect_baseline_summary()

        rule_performance = [{
            "rule_name": "broken_rule",
            "sample_count": 5,
            "avg_effectiveness": 0.2,
            "success_rate": 0.3,
        }]
        suggestions = advisor._deterministic_gap_analysis(baselines, rule_performance)
        unreachable = [s for s in suggestions if "unreachable" in s.get("reason", "")]
        assert len(unreachable) == 1

    def test_insufficient_samples_not_guarded(self, sqlite_db):
        """Rule with <3 samples is NOT guarded (insufficient evidence)."""
        engine = _make_engine([
            Rule(
                name="new_rule",
                description="Newly added",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=28, duration_seconds=60),
                action=RuleAction(target="relay2", action="set", value=True, reason="Too hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        _seed_baselines(memory, avg=25.0, std_dev=0.8)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        baselines = advisor._collect_baseline_summary()

        rule_performance = [{
            "rule_name": "new_rule",
            "sample_count": 2,
            "avg_effectiveness": 0.9,
            "success_rate": 1.0,
        }]
        suggestions = advisor._deterministic_gap_analysis(baselines, rule_performance)
        unreachable = [s for s in suggestions if "unreachable" in s.get("reason", "")]
        assert len(unreachable) == 1

    def test_no_performance_data_backward_compatible(self, sqlite_db):
        """Passing None for rule_performance preserves original behavior."""
        engine = _make_engine([
            Rule(
                name="compat_rule",
                description="Test backward compat",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=35, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        _seed_baselines(memory, avg=22.0, std_dev=1.0)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        baselines = advisor._collect_baseline_summary()

        suggestions = advisor._deterministic_gap_analysis(baselines, None)
        unreachable = [s for s in suggestions if "unreachable" in s.get("reason", "")]
        assert len(unreachable) == 1

    def test_too_sensitive_guarded(self, sqlite_db):
        """Effective rules are protected from too-sensitive suggestions."""
        engine = _make_engine([
            Rule(
                name="sensitive_effective",
                description="Fires too often but effective",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=21, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Warm"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        _seed_baselines(memory, avg=21.0, std_dev=1.0)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        baselines = advisor._collect_baseline_summary()

        rule_performance = [{
            "rule_name": "sensitive_effective",
            "sample_count": 10,
            "avg_effectiveness": 0.8,
            "success_rate": 0.9,
        }]
        suggestions = advisor._deterministic_gap_analysis(baselines, rule_performance)
        sensitive = [s for s in suggestions if "fires too often" in s.get("reason", "")]
        assert len(sensitive) == 0, "Too-sensitive should be guarded for effective rules"

    def test_effective_rule_skips_stale_forecast(self, sqlite_db):
        """Effective rule's stale forecast threshold is also skipped."""
        engine = _make_engine([
            Rule(
                name="forecast_rule",
                description="Preemptive cooling",
                condition=RuleCondition(
                    sensor="temp1", operator=">=", threshold=0,
                    forecast="will_exceed", forecast_threshold=30.0,
                    forecast_within_minutes=15,
                ),
                action=RuleAction(target="relay2", action="set", value=True, reason="Forecast hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        _seed_baselines(memory, avg=25.0, std_dev=0.8)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        baselines = advisor._collect_baseline_summary()

        rule_performance = [{
            "rule_name": "forecast_rule",
            "sample_count": 4,
            "avg_effectiveness": 0.6,
            "success_rate": 0.75,
        }]
        suggestions = advisor._deterministic_gap_analysis(baselines, rule_performance)
        forecast_suggestions = [s for s in suggestions if s["field"] == "forecast_threshold"]
        assert forecast_suggestions == [], "Effective rule's forecast should NOT be flagged as stale"

    def test_too_sensitive_capped(self, sqlite_db):
        """Too-sensitive adjustment is capped at ±25% of original threshold."""
        engine = _make_engine([
            Rule(
                name="lights_off_night",
                description="Turn off lights at night",
                condition=RuleCondition(sensor="light1", operator=">", threshold=100, duration_seconds=0),
                action=RuleAction(target="relay3", action="set", value=False, reason="Night"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=800, std=5 → uncapped suggestion would be ~810 (avg+2σ)
        # metric must match guess_sensor_type("light1") → "light_level"
        _seed_baselines(memory, avg=800.0, std_dev=5.0, metric="light_level")

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        baselines = advisor._collect_baseline_summary()

        # No rule_performance → guard inactive, too-sensitive fires
        suggestions = advisor._deterministic_gap_analysis(baselines, None)
        sensitive = [s for s in suggestions if "fires too often" in s.get("reason", "")]
        assert len(sensitive) == 1
        # Without cap: 810.0; with 25% cap: 100 + 100*0.25 = 125.0
        assert sensitive[0]["suggested_value"] == 125.0

    def test_too_sensitive_cap_with_zero_threshold(self, sqlite_db):
        """Cap handles threshold=0 gracefully (max_delta=0 → cap skipped)."""
        engine = _make_engine([
            Rule(
                name="zero_rule",
                description="Zero threshold rule",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=0, duration_seconds=0),
                action=RuleAction(target="relay1", action="set", value=True, reason="Test"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=21, std=1 → uncapped suggestion = 23.0; cap = 0*0.25 = 0 → skipped
        _seed_baselines(memory, avg=21.0, std_dev=1.0)

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)
        baselines = advisor._collect_baseline_summary()

        suggestions = advisor._deterministic_gap_analysis(baselines, None)
        sensitive = [s for s in suggestions if "fires too often" in s.get("reason", "")]
        assert len(sensitive) == 1
        # threshold=0, max_delta=0 → cap skipped, full suggestion applies
        assert sensitive[0]["suggested_value"] == 23.0


class TestMergeSuggestions:

    def test_deduplicates_prefers_higher_confidence(self, sqlite_db):
        """Same rule+field from both → keeps higher confidence."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)

        deterministic = [{
            "rule_name": "high_temp_alert",
            "field": "threshold",
            "suggested_value": 27.0,
            "reason": "Deterministic analysis",
            "confidence": 0.6,
        }]
        llm = [{
            "rule_name": "high_temp_alert",
            "field": "threshold",
            "suggested_value": 28.0,
            "reason": "LLM analysis",
            "confidence": 0.75,  # Higher by 0.15 (> 0.1 margin)
        }]

        merged = advisor._merge_suggestions(deterministic, llm)
        assert len(merged) == 1
        assert merged[0]["suggested_value"] == 28.0  # LLM wins, clearly higher

    def test_deduplicates_ties_prefer_deterministic(self, sqlite_db):
        """Same rule+field with similar confidence → keeps deterministic."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)

        deterministic = [{
            "rule_name": "high_temp_alert",
            "field": "threshold",
            "suggested_value": 27.0,
            "reason": "Deterministic analysis",
            "confidence": 0.7,
        }]
        llm = [{
            "rule_name": "high_temp_alert",
            "field": "threshold",
            "suggested_value": 28.0,
            "reason": "LLM analysis",
            "confidence": 0.75,  # Only 0.05 higher (within 0.1 margin)
        }]

        merged = advisor._merge_suggestions(deterministic, llm)
        assert len(merged) == 1
        assert merged[0]["suggested_value"] == 27.0  # Deterministic wins on tie

    def test_merges_different_fields(self, sqlite_db):
        """Different rule+field pairs are both kept."""
        engine = _make_engine()
        memory = CortexMemory(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, MagicMock(), engine)

        deterministic = [{
            "rule_name": "high_temp_alert",
            "field": "threshold",
            "suggested_value": 27.0,
            "reason": "Deterministic",
            "confidence": 0.6,
        }]
        llm = [{
            "rule_name": "high_temp_alert",
            "field": "duration_seconds",
            "suggested_value": 30,
            "reason": "LLM",
            "confidence": 0.7,
        }]

        merged = advisor._merge_suggestions(deterministic, llm)
        assert len(merged) == 2


class TestAnalyzeDeterministicWithoutLLM:

    def test_deterministic_without_llm(self, sqlite_db):
        """Ollama unavailable but baselines exist → still returns deterministic suggestions."""
        engine = _make_engine([
            Rule(
                name="unreachable_rule",
                description="Unreachable threshold",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=50, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Way too hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=22, std=1 → max upper = 24, threshold 50 is unreachable
        _seed_baselines(memory, avg=22.0, std_dev=1.0)

        mock_ollama = MagicMock()
        mock_ollama.is_available.return_value = False

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)
        result = advisor.analyze()

        assert len(result) >= 1
        assert any(r["field"] == "threshold" for r in result)
        mock_ollama.generate.assert_not_called()


class TestDuplicateSuggestionGuard:

    def test_second_run_skips_already_applied(self, sqlite_db):
        """Running analyze() twice produces suggestions only the first time."""
        engine = _make_engine([
            Rule(
                name="dup_rule",
                description="Unreachable threshold",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=50, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Way too hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        _seed_baselines(memory, avg=22.0, std_dev=1.0)

        mock_ollama = MagicMock()
        mock_ollama.is_available.return_value = False

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)

        # First run produces suggestions
        result1 = advisor.analyze()
        threshold_results1 = [r for r in result1 if r["field"] == "threshold"]
        assert len(threshold_results1) == 1

        # Second run skips because the same suggestion was already applied
        result2 = advisor.analyze()
        threshold_results2 = [r for r in result2 if r["field"] == "threshold"]
        assert threshold_results2 == []

    def test_second_run_skips_pending(self, sqlite_db):
        """Pending (non-auto-applied) suggestions also block duplicates."""
        engine = _make_engine([
            Rule(
                name="pending_rule",
                description="Fires too often",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=21, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Warm"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=10, effectiveness=0.3)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        # avg=21, std=1 → too-sensitive detection suggests ~23.0
        _seed_baselines(memory, avg=21.0, std_dev=1.0)

        # LLM also suggests 23.0 — but with low confidence so it stays pending
        mock_ollama = _make_ollama({
            "suggestions": [{
                "rule_name": "pending_rule",
                "field": "threshold",
                "suggested_value": 23.0,
                "reason": "LLM agrees",
                "confidence": 0.5,
            }]
        })

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)

        result1 = advisor.analyze()
        # Should have a suggestion for threshold=23.0
        thresh1 = [r for r in result1 if r["field"] == "threshold"]
        assert len(thresh1) == 1
        assert thresh1[0]["status"] == "pending"

        # Second run — same suggestion already pending → skipped
        result2 = advisor.analyze()
        thresh2 = [r for r in result2 if r["field"] == "threshold"]
        assert thresh2 == []

    def test_rejected_suggestion_allows_retry(self, sqlite_db):
        """After rejecting a suggestion, the same value can be suggested again."""
        engine = _make_engine([
            Rule(
                name="retry_rule",
                description="Unreachable threshold",
                condition=RuleCondition(sensor="temp1", operator=">", threshold=50, duration_seconds=10),
                action=RuleAction(target="relay1", action="set", value=True, reason="Way too hot"),
            ),
        ])
        memory = CortexMemory(sqlite_db)
        _seed_device(sqlite_db)
        _seed_outcomes(sqlite_db, count=0)
        outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

        _seed_baselines(memory, avg=22.0, std_dev=1.0)

        mock_ollama = MagicMock()
        mock_ollama.is_available.return_value = False

        advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)

        # First run
        result1 = advisor.analyze()
        thresh1 = [r for r in result1 if r["field"] == "threshold"]
        assert len(thresh1) == 1

        # Reject the suggestion and reset the in-memory threshold
        # (auto-apply already changed it from 50→24)
        sqlite_db.update_suggestion_status(thresh1[0]["id"], "rejected")
        engine.rules[0].condition.threshold = 50

        # Second run — rejected suggestion should allow a new one
        result2 = advisor.analyze()
        thresh2 = [r for r in result2 if r["field"] == "threshold"]
        assert len(thresh2) == 1


