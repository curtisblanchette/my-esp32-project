"""Tests for rules CRUD in SQLite, DecisionEngine loading, and YAML seed."""

import os
import sqlite3
import tempfile
import time

import pytest

from src.services.decision_engine import DecisionEngine


class TestRulesCRUD:
    """SQLite CRUD operations for cortex_rules."""

    def test_insert_and_get(self, sqlite_db):
        """Insert a rule and retrieve it by id."""
        rule = sqlite_db.insert_rule(
            name="test_rule",
            description="A test rule",
            condition={"sensor": "temp1", "operator": ">", "threshold": 25},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Hot"},
        )
        assert rule["id"]
        assert rule["name"] == "test_rule"
        assert rule["enabled"] is True
        assert rule["source"] == "user"
        assert rule["modified"] is False
        assert rule["condition"]["threshold"] == 25
        assert rule["action"]["target"] == "relay1"

        # Retrieve by id
        fetched = sqlite_db.get_rule(rule["id"])
        assert fetched == rule

    def test_get_rule_by_name(self, sqlite_db):
        """Retrieve a rule by name."""
        rule = sqlite_db.insert_rule(
            name="by_name_test", description="Test",
            condition={"sensor": "temp1", "operator": ">", "threshold": 20},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Test"},
        )
        fetched = sqlite_db.get_rule_by_name("by_name_test")
        assert fetched["id"] == rule["id"]

    def test_duplicate_name_raises(self, sqlite_db):
        """Inserting a rule with a duplicate name raises IntegrityError."""
        sqlite_db.insert_rule(
            name="unique_name", description="First",
            condition={"sensor": "temp1", "operator": ">", "threshold": 20},
            action={"target": "relay1", "action": "set", "value": True, "reason": "First"},
        )
        with pytest.raises(sqlite3.IntegrityError):
            sqlite_db.insert_rule(
                name="unique_name", description="Second",
                condition={"sensor": "temp1", "operator": ">", "threshold": 30},
                action={"target": "relay1", "action": "set", "value": True, "reason": "Second"},
            )

    def test_update_rule(self, sqlite_db):
        """Update a rule's fields."""
        rule = sqlite_db.insert_rule(
            name="update_me", description="Original",
            condition={"sensor": "temp1", "operator": ">", "threshold": 20},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Original"},
        )
        time.sleep(0.01)

        updated = sqlite_db.update_rule(
            rule["id"],
            name="renamed",
            description="Updated",
            condition={"sensor": "hum1", "operator": "<", "threshold": 30},
        )
        assert updated["name"] == "renamed"
        assert updated["description"] == "Updated"
        assert updated["condition"]["sensor"] == "hum1"
        assert updated["modified"] is True  # updated_at > created_at

    def test_update_nonexistent(self, sqlite_db):
        """Updating a nonexistent rule returns None."""
        result = sqlite_db.update_rule("nonexistent-id", name="nope")
        assert result is None

    def test_delete_rule(self, sqlite_db):
        """Delete a rule by id."""
        rule = sqlite_db.insert_rule(
            name="delete_me", description="Doomed",
            condition={"sensor": "temp1", "operator": ">", "threshold": 20},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Doomed"},
        )
        assert sqlite_db.delete_rule(rule["id"]) is True
        assert sqlite_db.get_rule(rule["id"]) is None

    def test_delete_nonexistent(self, sqlite_db):
        """Deleting a nonexistent rule returns False."""
        assert sqlite_db.delete_rule("nonexistent-id") is False

    def test_delete_cascades_suggestions(self, sqlite_db):
        """Deleting a rule cascade-deletes associated suggestions."""
        # Create suggestions table (normally done by RuleAdvisor)
        db = sqlite_db._get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS cortex_suggestions (
                id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                rule_name TEXT NOT NULL,
                field TEXT NOT NULL,
                current_value TEXT NOT NULL,
                suggested_value TEXT NOT NULL,
                reason TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                resolved_at INTEGER,
                outcome_sample_count INTEGER,
                observation_context TEXT
            );
        """)
        db.commit()

        rule = sqlite_db.insert_rule(
            name="cascade_test", description="Cascade",
            condition={"sensor": "temp1", "operator": ">", "threshold": 25},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Cascade"},
        )
        sqlite_db.insert_suggestion(
            id="s1", rule_name="cascade_test", field="threshold",
            current_value="25", suggested_value="27",
            reason="Test", confidence=0.7,
        )
        assert sqlite_db.get_suggestion("s1") is not None

        sqlite_db.delete_rule(rule["id"])
        assert sqlite_db.get_suggestion("s1") is None

    def test_get_all_rules(self, sqlite_db):
        """Get all rules ordered by created_at."""
        for i in range(3):
            sqlite_db.insert_rule(
                name=f"rule_{i}", description=f"Rule {i}",
                condition={"sensor": "temp1", "operator": ">", "threshold": 20 + i},
                action={"target": "relay1", "action": "set", "value": True, "reason": f"Rule {i}"},
            )

        rules = sqlite_db.get_all_rules()
        assert len(rules) == 3
        assert rules[0]["name"] == "rule_0"
        assert rules[2]["name"] == "rule_2"

    def test_count_rules(self, sqlite_db):
        """Count total rules."""
        assert sqlite_db.count_rules() == 0

        sqlite_db.insert_rule(
            name="count_test", description="Count",
            condition={"sensor": "temp1", "operator": ">", "threshold": 20},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Count"},
        )
        assert sqlite_db.count_rules() == 1

    def test_update_rule_enabled(self, sqlite_db):
        """Toggle a rule's enabled state."""
        rule = sqlite_db.insert_rule(
            name="toggle_test", description="Toggle",
            condition={"sensor": "temp1", "operator": ">", "threshold": 20},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Toggle"},
        )
        assert sqlite_db.update_rule_enabled(rule["id"], False) is True
        assert sqlite_db.get_rule(rule["id"])["enabled"] is False

        assert sqlite_db.update_rule_enabled(rule["id"], True) is True
        assert sqlite_db.get_rule(rule["id"])["enabled"] is True

    def test_update_rule_condition_field(self, sqlite_db):
        """Update a single condition field."""
        rule = sqlite_db.insert_rule(
            name="field_update", description="Field",
            condition={"sensor": "temp1", "operator": ">", "threshold": 25},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Field"},
        )
        assert sqlite_db.update_rule_condition_field(rule["id"], "threshold", 30) is True
        updated = sqlite_db.get_rule(rule["id"])
        assert updated["condition"]["threshold"] == 30

    def test_modified_flag_computed(self, sqlite_db):
        """Modified flag is computed from updated_at > created_at."""
        rule = sqlite_db.insert_rule(
            name="modified_test", description="Modified",
            condition={"sensor": "temp1", "operator": ">", "threshold": 20},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Modified"},
        )
        assert rule["modified"] is False

        time.sleep(0.01)
        sqlite_db.update_rule(rule["id"], description="Changed")
        updated = sqlite_db.get_rule(rule["id"])
        assert updated["modified"] is True


class TestRulesSeed:
    """YAML seed logic."""

    def test_seed_from_yaml(self, sqlite_db):
        """Seeds rules from YAML when table is empty."""
        rules_yaml = os.path.join(os.path.dirname(__file__), "..", "config", "rules.yaml")
        if not os.path.exists(rules_yaml):
            pytest.skip("rules.yaml not found")

        count = sqlite_db.seed_rules_from_yaml(rules_yaml)
        assert count > 0
        assert sqlite_db.count_rules() == count

        # All seeded rules have source='yaml'
        for rule in sqlite_db.get_all_rules():
            assert rule["source"] == "yaml"

    def test_seed_skips_nonempty(self, sqlite_db):
        """Seed does nothing when table already has rules."""
        sqlite_db.insert_rule(
            name="existing", description="Existing",
            condition={"sensor": "temp1", "operator": ">", "threshold": 20},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Existing"},
        )

        rules_yaml = os.path.join(os.path.dirname(__file__), "..", "config", "rules.yaml")
        if not os.path.exists(rules_yaml):
            pytest.skip("rules.yaml not found")

        count = sqlite_db.seed_rules_from_yaml(rules_yaml)
        assert count == 0
        assert sqlite_db.count_rules() == 1

    def test_seed_missing_file(self, sqlite_db):
        """Seed returns 0 when YAML file doesn't exist."""
        count = sqlite_db.seed_rules_from_yaml("/nonexistent/rules.yaml")
        assert count == 0


class TestEngineFromSqlite:
    """DecisionEngine loading from SQLite."""

    def test_from_sqlite_loads_rules(self, sqlite_db):
        """from_sqlite loads all fields including id."""
        sqlite_db.insert_rule(
            name="engine_test",
            description="Engine loading test",
            condition={"sensor": "temp1", "operator": ">", "threshold": 25, "duration_seconds": 10},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Engine test"},
        )

        engine = DecisionEngine.from_sqlite(sqlite_db)
        assert len(engine.rules) == 1
        rule = engine.rules[0]
        assert rule.name == "engine_test"
        assert rule.id  # Has a UUID
        assert rule.condition.sensor == "temp1"
        assert rule.condition.threshold == 25
        assert rule.condition.duration_seconds == 10
        assert rule.action.target == "relay1"

    def test_from_sqlite_empty_db(self, sqlite_db):
        """from_sqlite with no rules produces empty engine."""
        engine = DecisionEngine.from_sqlite(sqlite_db)
        assert engine.rules == []

    def test_reload_preserves_sensor_states(self, sqlite_db):
        """reload_from_sqlite preserves sensor_states and llm_config."""
        sqlite_db.insert_rule(
            name="reload_test", description="Reload",
            condition={"sensor": "temp1", "operator": ">", "threshold": 25},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Reload"},
        )

        engine = DecisionEngine.from_sqlite(sqlite_db)
        engine.llm_config = {"enabled": True}
        engine.sensor_states["test_key"] = object()

        # Add a new rule
        sqlite_db.insert_rule(
            name="new_rule", description="New",
            condition={"sensor": "hum1", "operator": ">", "threshold": 60},
            action={"target": "relay1", "action": "set", "value": True, "reason": "New"},
        )

        engine.reload_from_sqlite(sqlite_db)
        assert len(engine.rules) == 2
        assert engine.llm_config == {"enabled": True}
        assert "test_key" in engine.sensor_states

    def test_from_sqlite_preserves_enabled(self, sqlite_db):
        """Disabled rules stay disabled after loading."""
        rule = sqlite_db.insert_rule(
            name="disabled_rule", description="Disabled",
            condition={"sensor": "temp1", "operator": ">", "threshold": 25},
            action={"target": "relay1", "action": "set", "value": True, "reason": "Disabled"},
            enabled=False,
        )

        engine = DecisionEngine.from_sqlite(sqlite_db)
        assert engine.rules[0].enabled is False
