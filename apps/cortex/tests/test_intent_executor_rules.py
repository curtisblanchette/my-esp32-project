"""Tests for generate_rules, approve_rules, refine_rules intent handlers."""

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.chat_session import ChatSessionStore
from src.services.intent_executor import execute_intent, _format_proposed_rules


def run(coro):
    """Helper to run async coroutines in sync tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _event_loop():
    """Ensure a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture
def session_store():
    return ChatSessionStore()


@pytest.fixture
def mock_rule_generator():
    gen = MagicMock()
    return gen


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.reload_from_sqlite = MagicMock()
    return engine


@pytest.fixture
def mock_ws():
    ws = AsyncMock()
    ws.broadcast_rules = AsyncMock()
    return ws


@pytest.fixture
def mock_sqlite(sqlite_db):
    return sqlite_db


SAMPLE_RULES = [
    {
        "name": "high_temp_fan_on",
        "description": "Turn on fan when temperature exceeds 28°C",
        "condition": {
            "sensor": "temp1",
            "operator": ">",
            "threshold": 28,
            "duration_seconds": 30,
        },
        "action": {
            "target": "relay1",
            "action": "set",
            "value": True,
            "reason": "Temperature exceeded 28°C",
        },
    },
    {
        "name": "temp_restore_fan_off",
        "description": "Turn off fan when temperature drops below 25°C",
        "condition": {
            "sensor": "temp1",
            "operator": "<",
            "threshold": 25,
            "duration_seconds": 60,
        },
        "action": {
            "target": "relay1",
            "action": "set",
            "value": False,
            "reason": "Temperature dropped below 25°C",
        },
    },
]


class TestGenerateRulesIntent:

    def test_calls_generator_and_stores_in_session(
        self, mock_sqlite, session_store, mock_rule_generator, mock_ws,
    ):
        mock_rule_generator.generate_rules.return_value = {
            "rules": SAMPLE_RULES,
            "explanation": "Two temp rules",
            "error": None,
        }

        intent = {
            "intent": "generate_rules",
            "location": "grow-tent",
            "goal": "tomatoes in veg",
            "reply": "I'll generate rules for your grow tent.",
        }

        result = run(execute_intent(
            intent, source="chat", message="set up my grow tent",
            sqlite=mock_sqlite, redis=MagicMock(), mqtt=MagicMock(), ws=mock_ws,
            session_store=session_store, session_id="sess-1",
            rule_generator=mock_rule_generator,
        ))

        assert result["ok"] is True
        assert result["action"]["type"] == "proposed_rules"
        assert len(result["action"]["rules"]) == 2

        # Session has proposals
        proposed = session_store.get_proposed_rules("sess-1")
        assert proposed is not None
        assert len(proposed) == 2

        # Goal saved
        goal = mock_sqlite.get_location_goal("grow-tent")
        assert goal is not None
        assert goal["goal"] == "tomatoes in veg"

    def test_no_location_lists_available(self, mock_sqlite, mock_ws):
        mock_sqlite.upsert_device(
            id="esp32-1", location="grow-tent",
            capabilities={"sensors": [], "actuators": []},
        )

        intent = {
            "intent": "generate_rules",
            "goal": "tomatoes",
            "reply": "Which location?",
        }

        result = run(execute_intent(
            intent, source="chat", message="set up rules",
            sqlite=mock_sqlite, redis=MagicMock(), mqtt=MagicMock(), ws=mock_ws,
        ))

        assert result["ok"] is True
        assert "grow-tent" in result["reply"]

    def test_generator_error(
        self, mock_sqlite, session_store, mock_rule_generator, mock_ws,
    ):
        mock_rule_generator.generate_rules.return_value = {
            "rules": [],
            "explanation": "",
            "error": "No devices found",
        }

        intent = {
            "intent": "generate_rules",
            "location": "grow-tent",
            "goal": "tomatoes",
            "reply": "...",
        }

        result = run(execute_intent(
            intent, source="chat", message="set up",
            sqlite=mock_sqlite, redis=MagicMock(), mqtt=MagicMock(), ws=mock_ws,
            session_store=session_store, session_id="sess-1",
            rule_generator=mock_rule_generator,
        ))

        assert result["ok"] is False
        assert "No devices found" in result["reply"]

    def test_no_rule_generator(self, mock_sqlite, mock_ws):
        intent = {
            "intent": "generate_rules",
            "location": "grow-tent",
            "goal": "tomatoes",
            "reply": "...",
        }

        result = run(execute_intent(
            intent, source="chat", message="set up",
            sqlite=mock_sqlite, redis=MagicMock(), mqtt=MagicMock(), ws=mock_ws,
            rule_generator=None,
        ))

        assert result["ok"] is False
        assert "not available" in result["reply"]


class TestApproveRulesIntent:

    def test_inserts_rules_and_reloads_engine(
        self, mock_sqlite, session_store, mock_engine, mock_ws,
    ):
        session_store.set_proposed_rules("sess-1", SAMPLE_RULES)
        session = session_store.get_or_create("sess-1")
        session.location = "grow-tent"

        intent = {"intent": "approve_rules", "reply": "Activating rules."}

        result = run(execute_intent(
            intent, source="chat", message="approve",
            sqlite=mock_sqlite, redis=MagicMock(), mqtt=MagicMock(), ws=mock_ws,
            session_store=session_store, session_id="sess-1",
            engine=mock_engine,
        ))

        assert result["ok"] is True
        assert result["action"]["type"] == "rules_activated"
        assert result["action"]["count"] == 2

        # Rules inserted to DB
        all_rules = mock_sqlite.get_all_rules()
        generated = [r for r in all_rules if r["source"] == "generated"]
        assert len(generated) == 2

        # Engine reloaded
        mock_engine.reload_from_sqlite.assert_called_once_with(mock_sqlite)

        # WS broadcast
        mock_ws.broadcast_rules.assert_called_once()

        # Session cleared
        assert session_store.get_proposed_rules("sess-1") is None

    def test_no_pending_rules(self, mock_sqlite, session_store, mock_ws):
        intent = {"intent": "approve_rules", "reply": "..."}

        result = run(execute_intent(
            intent, source="chat", message="approve",
            sqlite=mock_sqlite, redis=MagicMock(), mqtt=MagicMock(), ws=mock_ws,
            session_store=session_store, session_id="sess-1",
        ))

        assert result["ok"] is True
        assert "No rules" in result["reply"]

    def test_no_session(self, mock_sqlite, mock_ws):
        intent = {"intent": "approve_rules", "reply": "..."}

        result = run(execute_intent(
            intent, source="chat", message="approve",
            sqlite=mock_sqlite, redis=MagicMock(), mqtt=MagicMock(), ws=mock_ws,
        ))

        assert result["ok"] is True
        assert "pending" in result["reply"].lower() or "don't have" in result["reply"].lower()

    def test_duplicate_name_skipped(
        self, mock_sqlite, session_store, mock_engine, mock_ws,
    ):
        # Pre-insert a rule with the same name
        mock_sqlite.insert_rule(
            name="high_temp_fan_on",
            description="existing",
            condition={"sensor": "temp1", "operator": ">", "threshold": 30},
            action={"target": "relay1", "action": "set", "value": True, "reason": "pre-existing"},
            enabled=True,
            source="user",
        )

        session_store.set_proposed_rules("sess-1", SAMPLE_RULES)
        session = session_store.get_or_create("sess-1")
        session.location = "grow-tent"

        intent = {"intent": "approve_rules", "reply": "..."}

        result = run(execute_intent(
            intent, source="chat", message="approve",
            sqlite=mock_sqlite, redis=MagicMock(), mqtt=MagicMock(), ws=mock_ws,
            session_store=session_store, session_id="sess-1",
            engine=mock_engine,
        ))

        assert result["ok"] is True
        assert result["action"]["count"] == 1
        assert "skipped" in result["reply"].lower() or "conflict" in result["reply"].lower()


class TestRefineRulesIntent:

    def test_refine_calls_generator_and_updates_session(
        self, mock_sqlite, session_store, mock_rule_generator, mock_ws,
    ):
        session_store.set_proposed_rules("sess-1", SAMPLE_RULES)
        session = session_store.get_or_create("sess-1")
        session.location = "grow-tent"
        session.goal = "tomatoes"

        refined_rules = [{**SAMPLE_RULES[0], "condition": {**SAMPLE_RULES[0]["condition"], "threshold": 27}}]
        mock_rule_generator.refine_rules.return_value = {
            "rules": refined_rules,
            "explanation": "Lowered threshold",
            "error": None,
        }

        intent = {
            "intent": "refine_rules",
            "refinement": "lower to 27",
            "reply": "Updating...",
        }

        result = run(execute_intent(
            intent, source="chat", message="lower to 27",
            sqlite=mock_sqlite, redis=MagicMock(), mqtt=MagicMock(), ws=mock_ws,
            session_store=session_store, session_id="sess-1",
            rule_generator=mock_rule_generator,
        ))

        assert result["ok"] is True
        assert result["action"]["type"] == "proposed_rules"

        # Session updated with refined rules
        proposed = session_store.get_proposed_rules("sess-1")
        assert proposed[0]["condition"]["threshold"] == 27

    def test_refine_no_pending(self, mock_sqlite, session_store, mock_ws):
        intent = {
            "intent": "refine_rules",
            "refinement": "change stuff",
            "reply": "...",
        }

        result = run(execute_intent(
            intent, source="chat", message="change stuff",
            sqlite=mock_sqlite, redis=MagicMock(), mqtt=MagicMock(), ws=mock_ws,
            session_store=session_store, session_id="sess-1",
        ))

        assert result["ok"] is True
        assert "No rules" in result["reply"] or "goals" in result["reply"].lower()


class TestFormatProposedRules:

    def test_format_basic_rules(self):
        result = _format_proposed_rules(SAMPLE_RULES)
        assert "Rule 1" in result
        assert "Rule 2" in result
        assert "Temperature" in result
        assert "relay1" in result
        assert "ON" in result
        assert "OFF" in result

    def test_format_empty_rules(self):
        result = _format_proposed_rules([])
        assert "No rules" in result

    def test_format_with_trend(self):
        rule = {
            "name": "trend_rule",
            "description": "Trend rule",
            "condition": {
                "sensor": "temp1",
                "operator": ">",
                "threshold": 25,
                "duration_seconds": 30,
                "trend": "rising",
            },
            "action": {
                "target": "relay1",
                "action": "set",
                "value": True,
                "reason": "test",
            },
        }
        result = _format_proposed_rules([rule])
        assert "trending rising" in result

    def test_format_with_forecast(self):
        rule = {
            "name": "forecast_rule",
            "description": "Forecast rule",
            "condition": {
                "sensor": "temp1",
                "operator": ">",
                "threshold": 25,
                "forecast": "will_exceed",
                "forecast_threshold": 30,
            },
            "action": {
                "target": "relay1",
                "action": "set",
                "value": True,
                "reason": "test",
            },
        }
        result = _format_proposed_rules([rule])
        assert "will exceed" in result
        assert "30" in result

    def test_format_soil_moisture_sensor(self):
        rule = {
            "name": "dry_soil",
            "description": "Water when dry",
            "condition": {"sensor": "soil1", "operator": "<", "threshold": 60, "duration_seconds": 120},
            "action": {"target": "drip1", "action": "set", "value": True, "reason": "Soil dry"},
        }
        result = _format_proposed_rules([rule])
        assert "Soil Moisture" in result
        assert "60%" in result

    def test_format_contact_sensor(self):
        rule = {
            "name": "door_open",
            "description": "Lights on when door opens",
            "condition": {"sensor": "contact1", "operator": "==", "threshold": 1},
            "action": {"target": "lights1", "action": "set", "value": True, "reason": "Door opened"},
        }
        result = _format_proposed_rules([rule])
        assert "Contact" in result
        # Contact sensors have empty unit, so threshold should be bare number
        assert "1" in result

    def test_format_pulse_action(self):
        rule = {
            "name": "auto_close",
            "description": "Auto-close garage",
            "condition": {"sensor": "contact1", "operator": "==", "threshold": 1, "duration_seconds": 600},
            "action": {"target": "garage1", "action": "pulse", "value": True, "reason": "Close garage"},
        }
        result = _format_proposed_rules([rule])
        assert "garage1 PULSE" in result
