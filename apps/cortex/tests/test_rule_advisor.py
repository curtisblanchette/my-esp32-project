"""Tests for the Rule Advisor service."""

import json
import time
from unittest.mock import MagicMock

import pytest

from src.services.rule_advisor import RuleAdvisor, AUTO_APPLY_CONFIDENCE
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

    def test_analyze_with_no_outcomes(self, sqlite_db):
        """Returns empty list when no outcome data exists."""
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
