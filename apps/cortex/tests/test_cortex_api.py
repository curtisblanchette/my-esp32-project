"""Tests for the Cortex intelligence API routes."""

import json
import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.cortex import create_cortex_router
from src.services.cortex_memory import CortexMemory
from src.services.outcome_tracker import OutcomeTracker
from src.services.rule_advisor import RuleAdvisor
from src.services.decision_engine import DecisionEngine, Rule, RuleCondition, RuleAction


def _make_engine():
    engine = DecisionEngine()
    engine.rules = [
        Rule(
            name="high_temp_alert",
            description="Turn on fan when temp > 25",
            condition=RuleCondition(sensor="temp1", operator=">", threshold=25, duration_seconds=15),
            action=RuleAction(target="relay1", action="set", value=True, reason="Temperature exceeded 25°C"),
        ),
    ]
    return engine


def _seed_outcomes(sqlite_db, count=5):
    """Seed the cortex_outcomes table."""
    db = sqlite_db._get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS cortex_outcomes (
            correlation_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL, target TEXT NOT NULL,
            action TEXT NOT NULL, value JSON, reason TEXT,
            command_ts INTEGER NOT NULL, ack_status TEXT NOT NULL,
            target_metric TEXT NOT NULL, desired_direction TEXT NOT NULL,
            pre_value REAL NOT NULL, post_1m REAL, post_5m REAL,
            post_10m REAL, effectiveness REAL NOT NULL, scored_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_outcomes_device_target ON cortex_outcomes(device_id, target);
        CREATE INDEX IF NOT EXISTS idx_outcomes_ts ON cortex_outcomes(command_ts);
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
                json.dumps(True), "Temperature exceeded 25°C", now - (i * 60000),
                "executed", "temperature", "decrease",
                26.0, 25.5, 24.8, 24.0, 0.5, now,
            ),
        )
    db.commit()


def _seed_device(sqlite_db, device_id="esp32-test"):
    sqlite_db.upsert_device(
        id=device_id, location="room1",
        capabilities={"sensors": [{"id": "temp1", "type": "temperature"}], "actuators": []},
    )


@pytest.fixture
def cortex_client(sqlite_db):
    """Create a TestClient with the cortex router mounted."""
    engine = _make_engine()
    memory = CortexMemory(sqlite_db)
    _seed_outcomes(sqlite_db)
    _seed_device(sqlite_db)
    outcome_tracker = OutcomeTracker(sqlite_db, MagicMock())

    mock_ollama = MagicMock()
    mock_ollama.is_available.return_value = True
    mock_ollama.generate.return_value = json.dumps({"suggestions": []})

    rule_advisor = RuleAdvisor(sqlite_db, outcome_tracker, memory, mock_ollama, engine)

    app = FastAPI()
    app.include_router(
        create_cortex_router(sqlite_db, outcome_tracker, memory, rule_advisor, engine=engine),
        prefix="/api/cortex",
    )
    return TestClient(app), sqlite_db, rule_advisor, engine


class TestCortexRoutes:

    def test_get_status(self, cortex_client):
        """Status endpoint returns overview with counts."""
        client, _, _, _ = cortex_client
        r = client.get("/api/cortex/status")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["outcomes"]["total"] == 5
        assert data["baselines"]["total"] >= 0
        assert "pending" in data["suggestions"]

    def test_get_baselines(self, cortex_client):
        """Returns hourly baselines for a device."""
        client, sqlite_db, _, _ = cortex_client

        # Seed some baselines
        memory = CortexMemory(sqlite_db)
        for hour in range(3):
            memory.update_baseline("esp32-test", "temperature", hour, 22.0 + hour)

        r = client.get("/api/cortex/baselines/esp32-test")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["baselines"]) == 3

    def test_get_baselines_empty_device(self, cortex_client):
        """Returns empty list for a device with no baselines."""
        client, _, _, _ = cortex_client
        r = client.get("/api/cortex/baselines/nonexistent")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["baselines"] == []

    def test_get_adjustments_all(self, cortex_client):
        """Returns all suggestions."""
        client, sqlite_db, _, _ = cortex_client

        # Seed a suggestion
        sqlite_db.insert_suggestion(
            id="test-1", rule_name="high_temp_alert", field="threshold",
            current_value="25", suggested_value="27",
            reason="Test", confidence=0.7,
        )

        r = client.get("/api/cortex/adjustments")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["adjustments"]) == 1
        assert data["adjustments"][0]["id"] == "test-1"

    def test_get_adjustments_filtered(self, cortex_client):
        """Filtering by status works."""
        client, sqlite_db, _, _ = cortex_client

        sqlite_db.insert_suggestion(
            id="pending-1", rule_name="high_temp_alert", field="threshold",
            current_value="25", suggested_value="27",
            reason="Pending one", confidence=0.7,
        )
        sqlite_db.insert_suggestion(
            id="rejected-1", rule_name="high_temp_alert", field="threshold",
            current_value="25", suggested_value="30",
            reason="Rejected one", confidence=0.5,
        )
        sqlite_db.update_suggestion_status("rejected-1", "rejected")

        r = client.get("/api/cortex/adjustments?status=pending")
        data = r.json()
        assert len(data["adjustments"]) == 1
        assert data["adjustments"][0]["id"] == "pending-1"

    def test_approve_adjustment(self, cortex_client):
        """POST approve applies the suggestion and changes the rule."""
        client, sqlite_db, advisor, engine = cortex_client

        sqlite_db.insert_suggestion(
            id="approve-1", rule_name="high_temp_alert", field="threshold",
            current_value=json.dumps(25), suggested_value=json.dumps(27),
            reason="Better threshold", confidence=0.7,
        )

        r = client.post("/api/cortex/adjustments/approve-1", json={"action": "approve"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Rule should be modified
        assert engine.rules[0].condition.threshold == 27

        # Status should be applied
        stored = sqlite_db.get_suggestion("approve-1")
        assert stored["status"] == "applied"

    def test_reject_adjustment(self, cortex_client):
        """POST reject updates status without modifying rules."""
        client, sqlite_db, _, engine = cortex_client

        sqlite_db.insert_suggestion(
            id="reject-1", rule_name="high_temp_alert", field="threshold",
            current_value=json.dumps(25), suggested_value=json.dumps(30),
            reason="Too aggressive", confidence=0.5,
        )

        r = client.post("/api/cortex/adjustments/reject-1", json={"action": "reject"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Rule unchanged
        assert engine.rules[0].condition.threshold == 25
        stored = sqlite_db.get_suggestion("reject-1")
        assert stored["status"] == "rejected"

    def test_approve_nonexistent(self, cortex_client):
        """Approving a nonexistent suggestion returns ok=false."""
        client, _, _, _ = cortex_client
        r = client.post("/api/cortex/adjustments/nope", json={"action": "approve"})
        assert r.status_code == 200
        assert r.json()["ok"] is False

    def test_run_advisor_manually(self, cortex_client):
        """POST /advisor/run triggers analysis and returns suggestions."""
        client, _, advisor, _ = cortex_client
        r = client.post("/api/cortex/advisor/run")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "suggestions" in data
        assert "count" in data
        assert isinstance(data["suggestions"], list)

    # ── Rules Endpoints ────────────────────────────────────────────────

    def test_get_rules(self, cortex_client):
        """GET /rules returns all in-memory rules."""
        client, _, _, engine = cortex_client
        r = client.get("/api/cortex/rules")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["rules"]) == len(engine.rules)

        rule = data["rules"][0]
        assert rule["name"] == "high_temp_alert"
        assert rule["enabled"] is True
        assert rule["modified"] is False
        assert rule["condition"]["sensor"] == "temp1"
        assert rule["condition"]["operator"] == ">"
        assert rule["condition"]["threshold"] == 25
        assert rule["action"]["target"] == "relay1"
        assert rule["action"]["value"] is True

    def test_get_rules_includes_modified_flag(self, cortex_client):
        """Modified rules are flagged in the response."""
        client, _, _, engine = cortex_client
        engine.modified_rules.add("high_temp_alert")

        r = client.get("/api/cortex/rules")
        data = r.json()
        assert data["rules"][0]["modified"] is True

    def test_toggle_rule_disable(self, cortex_client):
        """PATCH disables a rule."""
        client, _, _, engine = cortex_client
        assert engine.rules[0].enabled is True

        r = client.patch("/api/cortex/rules/high_temp_alert", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert engine.rules[0].enabled is False

    def test_toggle_rule_enable(self, cortex_client):
        """PATCH re-enables a disabled rule."""
        client, _, _, engine = cortex_client
        engine.rules[0].enabled = False

        r = client.patch("/api/cortex/rules/high_temp_alert", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert engine.rules[0].enabled is True

    def test_toggle_nonexistent_rule(self, cortex_client):
        """PATCH on unknown rule name returns 404."""
        client, _, _, _ = cortex_client
        r = client.patch("/api/cortex/rules/nonexistent_rule", json={"enabled": False})
        assert r.status_code == 404
        assert r.json()["ok"] is False
