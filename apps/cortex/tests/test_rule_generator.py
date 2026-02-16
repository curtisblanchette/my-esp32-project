"""Tests for RuleGenerator."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.rule_generator import RuleGenerator
from src.services.sqlite_client import (
    Device,
    DeviceCapabilities,
    Sensor,
    Actuator,
)


def _make_device(
    device_id="esp32-grow",
    location="grow-tent",
    sensors=None,
    actuators=None,
    online=True,
):
    """Helper to create a Device with capabilities."""
    if sensors is None:
        sensors = [Sensor(id="temp1", type="DHT22", name="Temperature")]
    if actuators is None:
        actuators = [Actuator(id="relay1", type="relay", pin=5, name="Fan")]
    return Device(
        id=device_id,
        location=location,
        name=device_id,
        capabilities=DeviceCapabilities(sensors=sensors, actuators=actuators),
        online=online,
        last_seen=0,
        created_at=0,
        updated_at=0,
        display_order=0,
    )


def _make_generator(devices=None, baselines=None, llm_response=None):
    """Create a RuleGenerator with mocked dependencies."""
    sqlite = MagicMock()
    sqlite.get_all_devices.return_value = devices or []

    memory = MagicMock()
    memory.get_all_baselines.return_value = baselines or []

    ollama = MagicMock()
    if llm_response is not None:
        ollama.generate.return_value = json.dumps(llm_response)

    ws = MagicMock()
    ws.get_latest_by_device.return_value = None

    return RuleGenerator(sqlite, memory, ollama, ws), sqlite, memory, ollama


VALID_RULES_RESPONSE = {
    "rules": [
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
    ],
    "explanation": "Two rules for temperature control",
}


class TestRuleGenerator:
    """Tests for LLM-powered rule generation."""

    def test_generate_rules_happy_path(self):
        device = _make_device()
        gen, _, _, _ = _make_generator(
            devices=[device],
            llm_response=VALID_RULES_RESPONSE,
        )
        result = gen.generate_rules("grow-tent", "tomatoes in veg")
        assert result["error"] is None
        assert len(result["rules"]) == 2
        assert result["rules"][0]["name"] == "high_temp_fan_on"
        assert result["rules"][1]["name"] == "temp_restore_fan_off"
        assert result["explanation"] == "Two rules for temperature control"

    def test_generate_rules_no_devices(self):
        gen, _, _, _ = _make_generator(devices=[])
        result = gen.generate_rules("grow-tent", "tomatoes")
        assert result["error"] is not None
        assert "No devices found" in result["error"]
        assert result["rules"] == []

    def test_generate_rules_no_actuators(self):
        device = _make_device(actuators=[])
        gen, _, _, _ = _make_generator(devices=[device])
        result = gen.generate_rules("grow-tent", "tomatoes")
        assert result["error"] is not None
        assert "No actuators found" in result["error"]

    def test_generate_rules_filters_wrong_location(self):
        device = _make_device(location="server-room")
        gen, _, _, _ = _make_generator(devices=[device])
        result = gen.generate_rules("grow-tent", "tomatoes")
        assert "No devices found" in result["error"]

    def test_generate_rules_case_insensitive_location(self):
        """Location matching should be case-insensitive."""
        device = _make_device(location="Garage")
        gen, _, _, _ = _make_generator(
            devices=[device],
            llm_response=VALID_RULES_RESPONSE,
        )
        result = gen.generate_rules("garage", "open at 7am close at 9pm")
        assert result["error"] is None
        assert len(result["rules"]) == 2

    def test_generate_rules_malformed_llm_response(self):
        gen, sqlite, memory, ollama = _make_generator(
            devices=[_make_device()],
        )
        ollama.generate.return_value = "not valid json {"
        result = gen.generate_rules("grow-tent", "tomatoes")
        assert result["error"] is not None
        assert "invalid JSON" in result["error"]

    def test_generate_rules_strips_invalid_sensor(self):
        """Rules referencing sensors that don't exist get dropped."""
        response = {
            "rules": [
                {
                    "name": "bad_sensor_rule",
                    "description": "Uses fake sensor",
                    "condition": {
                        "sensor": "fake_sensor",
                        "operator": ">",
                        "threshold": 30,
                    },
                    "action": {
                        "target": "relay1",
                        "action": "set",
                        "value": True,
                        "reason": "test",
                    },
                },
                VALID_RULES_RESPONSE["rules"][0],
            ],
            "explanation": "one bad, one good",
        }
        gen, _, _, _ = _make_generator(
            devices=[_make_device()],
            llm_response=response,
        )
        result = gen.generate_rules("grow-tent", "tomatoes")
        assert result["error"] is None
        assert len(result["rules"]) == 1
        assert result["rules"][0]["name"] == "high_temp_fan_on"

    def test_generate_rules_strips_invalid_actuator(self):
        """Rules referencing actuators that don't exist get dropped."""
        response = {
            "rules": [
                {
                    "name": "bad_actuator_rule",
                    "description": "Uses fake actuator",
                    "condition": {
                        "sensor": "temp1",
                        "operator": ">",
                        "threshold": 30,
                    },
                    "action": {
                        "target": "fake_relay",
                        "action": "set",
                        "value": True,
                        "reason": "test",
                    },
                },
            ],
            "explanation": "bad actuator",
        }
        gen, _, _, _ = _make_generator(
            devices=[_make_device()],
            llm_response=response,
        )
        result = gen.generate_rules("grow-tent", "tomatoes")
        assert result["error"] is not None
        assert "all were invalid" in result["error"]
        assert "relay1" in result["error"]  # shows available actuators
        assert len(result["rules"]) == 0

    def test_generate_rules_strips_invalid_operator(self):
        response = {
            "rules": [
                {
                    "name": "bad_op_rule",
                    "description": "Bad operator",
                    "condition": {
                        "sensor": "temp1",
                        "operator": "LIKE",
                        "threshold": 30,
                    },
                    "action": {
                        "target": "relay1",
                        "action": "set",
                        "value": True,
                        "reason": "test",
                    },
                },
            ],
            "explanation": "",
        }
        gen, _, _, _ = _make_generator(
            devices=[_make_device()],
            llm_response=response,
        )
        result = gen.generate_rules("grow-tent", "tomatoes")
        assert len(result["rules"]) == 0
        assert result["error"] is not None
        assert "all were invalid" in result["error"]

    def test_generate_rules_with_baselines(self):
        device = _make_device()
        baselines = [
            {
                "deviceId": "esp32-grow",
                "metric": "temperature",
                "hour": 14,
                "avg": 23.5,
                "stdDev": 1.2,
                "sampleCount": 100,
            },
        ]
        gen, _, _, ollama = _make_generator(
            devices=[device],
            baselines=baselines,
            llm_response=VALID_RULES_RESPONSE,
        )
        result = gen.generate_rules("grow-tent", "tomatoes")
        assert result["error"] is None
        # Verify baselines were included in the prompt
        call_args = ollama.generate.call_args
        prompt = call_args[0][0]
        assert "Learned baselines" in prompt
        assert "23.5" in prompt

    def test_generate_rules_default_duration(self):
        """Rules without duration_seconds get a default of 30."""
        response = {
            "rules": [
                {
                    "name": "no_duration",
                    "description": "Missing duration",
                    "condition": {
                        "sensor": "temp1",
                        "operator": ">",
                        "threshold": 28,
                    },
                    "action": {
                        "target": "relay1",
                        "action": "set",
                        "value": True,
                        "reason": "test",
                    },
                },
            ],
            "explanation": "",
        }
        gen, _, _, _ = _make_generator(
            devices=[_make_device()],
            llm_response=response,
        )
        result = gen.generate_rules("grow-tent", "tomatoes")
        assert result["rules"][0]["condition"]["duration_seconds"] == 30

    def test_generate_rules_cleans_null_optionals(self):
        """Null optional fields are removed from rule dicts."""
        response = {
            "rules": [
                {
                    "name": "with_nulls",
                    "description": "Has null fields",
                    "condition": {
                        "sensor": "temp1",
                        "operator": ">",
                        "threshold": 28,
                        "duration_seconds": 30,
                        "trend": None,
                        "forecast": None,
                        "baseline_deviation": None,
                    },
                    "action": {
                        "target": "relay1",
                        "action": "set",
                        "value": True,
                        "reason": "test",
                    },
                },
            ],
            "explanation": "",
        }
        gen, _, _, _ = _make_generator(
            devices=[_make_device()],
            llm_response=response,
        )
        result = gen.generate_rules("grow-tent", "tomatoes")
        cond = result["rules"][0]["condition"]
        assert "trend" not in cond
        assert "forecast" not in cond
        assert "baseline_deviation" not in cond

    def test_refine_rules_includes_current_rules(self):
        device = _make_device()
        gen, _, _, ollama = _make_generator(
            devices=[device],
            llm_response=VALID_RULES_RESPONSE,
        )
        current = [VALID_RULES_RESPONSE["rules"][0]]
        result = gen.refine_rules(
            current, "lower the threshold to 27", "grow-tent", "tomatoes"
        )
        assert result["error"] is None
        call_args = ollama.generate.call_args
        prompt = call_args[0][0]
        assert "Current proposed rules:" in prompt
        assert "lower the threshold to 27" in prompt

    def test_generate_rules_ollama_exception(self):
        gen, sqlite, memory, ollama = _make_generator(
            devices=[_make_device()],
        )
        ollama.generate.side_effect = Exception("Ollama down")
        result = gen.generate_rules("grow-tent", "tomatoes")
        assert result["error"] is not None
        assert "failed" in result["error"].lower()

    def test_build_generation_context_structure(self):
        device = _make_device(
            sensors=[
                Sensor(id="temp1", type="DHT22", name="Temperature"),
                Sensor(id="hum1", type="DHT22", name="Humidity"),
            ],
            actuators=[
                Actuator(id="relay1", type="relay", pin=5, name="Fan"),
                Actuator(id="relay2", type="relay", pin=6, name="Light"),
            ],
        )
        gen, _, _, _ = _make_generator(devices=[device])
        ctx = gen._build_generation_context("grow-tent", "tomatoes")
        assert ctx["location"] == "grow-tent"
        assert ctx["goal"] == "tomatoes"
        assert len(ctx["devices"]) == 1
        assert ctx["sensor_ids"] == {"temp1", "hum1"}
        assert ctx["actuators"] == {"relay1", "relay2"}

    def test_validate_forecast_needs_threshold(self):
        """Rule with forecast but no forecast_threshold should be rejected."""
        response = {
            "rules": [
                {
                    "name": "bad_forecast",
                    "description": "Missing forecast threshold",
                    "condition": {
                        "sensor": "temp1",
                        "operator": ">",
                        "threshold": 25,
                        "forecast": "will_exceed",
                    },
                    "action": {
                        "target": "relay1",
                        "action": "set",
                        "value": True,
                        "reason": "test",
                    },
                },
            ],
            "explanation": "",
        }
        gen, _, _, _ = _make_generator(
            devices=[_make_device()],
            llm_response=response,
        )
        result = gen.generate_rules("grow-tent", "tomatoes")
        assert len(result["rules"]) == 0

    def test_generate_rules_diverse_sensor_types(self):
        """Rules with soil_moisture and contact sensors validate correctly."""
        device = _make_device(
            sensors=[
                Sensor(id="soil1", type="soil_moisture", name="Bed 1"),
                Sensor(id="contact1", type="contact", name="Door"),
            ],
            actuators=[
                Actuator(id="drip1", type="switch", pin=5, name="Drip"),
                Actuator(id="garage1", type="momentary", pin=6, name="Garage"),
            ],
        )
        response = {
            "rules": [
                {
                    "name": "dry_soil_water",
                    "description": "Water when soil is dry",
                    "condition": {"sensor": "soil1", "operator": "<", "threshold": 60, "duration_seconds": 120},
                    "action": {"target": "drip1", "action": "set", "value": True, "reason": "Soil dry"},
                },
                {
                    "name": "door_open_close",
                    "description": "Auto-close garage after 10 min",
                    "condition": {"sensor": "contact1", "operator": "==", "threshold": 1, "duration_seconds": 600},
                    "action": {"target": "garage1", "action": "pulse", "value": True, "reason": "Door open too long"},
                },
            ],
            "explanation": "Grow + security rules",
        }
        gen, _, _, _ = _make_generator(devices=[device], llm_response=response)
        result = gen.generate_rules("grow-tent", "cannabis + security")
        assert result["error"] is None
        assert len(result["rules"]) == 2
        assert result["rules"][0]["name"] == "dry_soil_water"
        assert result["rules"][1]["action"]["action"] == "pulse"

    def test_build_context_generic_readings(self):
        """Current readings are formatted generically for all keys."""
        device = _make_device(
            sensors=[
                Sensor(id="temp1", type="temperature", unit="celsius"),
                Sensor(id="soil1", type="soil_moisture"),
            ],
        )
        gen, _, _, _ = _make_generator(devices=[device])
        gen._ws.get_latest_by_device.return_value = {
            "temp": 24.5,
            "soil1": 72.0,
            "updatedAt": 1000,
            "deviceId": "esp32-grow",
        }
        ctx = gen._build_generation_context("grow-tent", "cannabis")
        section = ctx["device_sections"][0]
        assert "temp=24.5°C" in section
        assert "soil1=72.0%" in section
        assert "updatedAt" not in section

    def test_baseline_units_use_sensor_meta(self):
        """Baseline summary uses sensor_meta for unit resolution."""
        device = _make_device()
        baselines = [
            {"metric": "temperature", "hour": 12, "avg": 24.0, "stdDev": 1.5, "sampleCount": 50},
            {"metric": "soil_moisture", "hour": 12, "avg": 65.0, "stdDev": 5.0, "sampleCount": 20},
        ]
        gen, _, _, _ = _make_generator(devices=[device], baselines=baselines)
        ctx = gen._build_generation_context("grow-tent", "test")
        prompt = gen._format_generation_prompt(ctx)
        assert "temperature: avg=24.0°C" in prompt
        assert "soil_moisture: avg=65.0%" in prompt
