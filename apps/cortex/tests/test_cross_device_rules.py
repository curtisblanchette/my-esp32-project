"""Unit tests for Phase 5 cross-device rule evaluation."""

import time
import tempfile
import os

from src.services.decision_engine import (
    DecisionEngine,
    Rule,
    RuleCondition,
    RuleAction,
)
from src.models.telemetry import TelemetryMessage, Reading


# ── Mock Coordinator ──────────────────────────────────────────────────


class MockCoordinator:
    """Controllable coordinator for tests."""

    def __init__(
        self,
        device_readings: dict[str, dict[str, float]] | None = None,
        online_devices: list[str] | None = None,
        device_actuators: dict[str, list[str]] | None = None,
        device_locations: dict[str, str] | None = None,
    ):
        self._readings = device_readings or {}
        self._online = online_devices or []
        self._actuators = device_actuators or {}
        self._locations = device_locations or {}
        # Minimal sqlite mock for _build_commands specific-device path
        self._sqlite = _MockSqliteForCoordinator(
            self._online, self._actuators, self._locations,
        )

    def get_online_device_ids(self):
        return list(self._online)

    def get_latest_reading(self, device_id, sensor_id):
        return self._readings.get(device_id, {}).get(sensor_id)

    def get_all_latest_readings(self, sensor_id):
        return {
            did: self._readings[did][sensor_id]
            for did in self._online
            if did in self._readings and sensor_id in self._readings[did]
        }

    def device_has_actuator(self, device_id, actuator_id):
        return actuator_id in self._actuators.get(device_id, [])

    def get_devices_with_actuator(self, actuator_id):
        return [
            (did, self._locations.get(did, "room1"))
            for did in self._online
            if actuator_id in self._actuators.get(did, [])
        ]


class _MockDevice:
    def __init__(self, id, location, online):
        self.id = id
        self.location = location
        self.online = online


class _MockSqliteForCoordinator:
    """Minimal mock used by _build_commands for specific device_id target_scope."""

    def __init__(self, online, actuators, locations):
        self._online = online
        self._actuators = actuators
        self._locations = locations

    def get_device(self, device_id):
        if device_id in self._online:
            return _MockDevice(device_id, self._locations.get(device_id, "room1"), True)
        # Check if device exists but is offline
        if device_id in self._locations:
            return _MockDevice(device_id, self._locations[device_id], False)
        return None


# ── Helpers ───────────────────────────────────────────────────────────


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
    scope="self",
    target_scope="self",
    reason="test reason",
):
    return Rule(
        name=name,
        description="test",
        condition=RuleCondition(
            sensor=sensor,
            operator=operator,
            threshold=threshold,
            duration_seconds=duration_seconds,
            scope=scope,
        ),
        action=RuleAction(
            target=target,
            action="set",
            value=value,
            reason=reason,
            target_scope=target_scope,
        ),
    )


# ── Scope: any ────────────────────────────────────────────────────────


class TestScopeAny:
    def test_triggers_when_one_device_exceeds(self):
        """scope: any triggers when max(values) > threshold."""
        engine = DecisionEngine(rules=[_make_rule(scope="any", threshold=25.0)])
        coord = MockCoordinator(
            device_readings={"a": {"temp1": 20.0}, "b": {"temp1": 30.0}},
            online_devices=["a", "b"],
            device_actuators={"a": ["relay1"], "b": ["relay1"]},
        )
        telemetry = _make_telemetry(temp=22.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 1

    def test_no_trigger_when_none_exceed(self):
        """scope: any does not trigger when no device exceeds threshold."""
        engine = DecisionEngine(rules=[_make_rule(scope="any", threshold=25.0)])
        coord = MockCoordinator(
            device_readings={"a": {"temp1": 20.0}, "b": {"temp1": 22.0}},
            online_devices=["a", "b"],
        )
        telemetry = _make_telemetry(temp=22.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 0

    def test_less_than_operator(self):
        """scope: any with operator < triggers when min(values) < threshold."""
        engine = DecisionEngine(rules=[
            _make_rule(scope="any", operator="<", threshold=20.0),
        ])
        coord = MockCoordinator(
            device_readings={"a": {"temp1": 25.0}, "b": {"temp1": 18.0}},
            online_devices=["a", "b"],
        )
        telemetry = _make_telemetry(temp=22.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 1

    def test_without_coordinator_skips(self):
        """scope: any without coordinator produces no commands."""
        engine = DecisionEngine(rules=[_make_rule(scope="any")])
        telemetry = _make_telemetry(temp=30.0)
        cmds = engine.evaluate(telemetry, coordinator=None)
        assert len(cmds) == 0

    def test_no_online_devices_skips(self):
        """scope: any with empty device list produces no commands."""
        engine = DecisionEngine(rules=[_make_rule(scope="any")])
        coord = MockCoordinator(device_readings={}, online_devices=[])
        telemetry = _make_telemetry(temp=30.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 0


# ── Scope: all ────────────────────────────────────────────────────────


class TestScopeAll:
    def test_triggers_when_all_exceed(self):
        """scope: all triggers when min(values) > threshold."""
        engine = DecisionEngine(rules=[_make_rule(scope="all", threshold=25.0)])
        coord = MockCoordinator(
            device_readings={"a": {"temp1": 26.0}, "b": {"temp1": 28.0}},
            online_devices=["a", "b"],
            device_actuators={"a": ["relay1"], "b": ["relay1"]},
        )
        telemetry = _make_telemetry(temp=22.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 1

    def test_no_trigger_when_one_below(self):
        """scope: all does not trigger when min(values) < threshold."""
        engine = DecisionEngine(rules=[_make_rule(scope="all", threshold=25.0)])
        coord = MockCoordinator(
            device_readings={"a": {"temp1": 24.0}, "b": {"temp1": 28.0}},
            online_devices=["a", "b"],
        )
        telemetry = _make_telemetry(temp=22.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 0


# ── Scope: specific device_id ─────────────────────────────────────────


class TestScopeSpecificDevice:
    def test_reads_from_specific_device(self):
        """scope: <device_id> reads from that device's latest reading."""
        engine = DecisionEngine(rules=[
            _make_rule(scope="esp32-garage", threshold=30.0),
        ])
        coord = MockCoordinator(
            device_readings={"esp32-garage": {"temp1": 35.0}},
            online_devices=["esp32-garage"],
            device_actuators={"esp32-garage": ["relay1"]},
        )
        telemetry = _make_telemetry(temp=22.0, device_id="esp32-test")
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 1

    def test_missing_device(self):
        """scope: <unknown_device> produces no commands."""
        engine = DecisionEngine(rules=[
            _make_rule(scope="esp32-missing", threshold=25.0),
        ])
        coord = MockCoordinator(
            device_readings={},
            online_devices=[],
        )
        telemetry = _make_telemetry(temp=30.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 0


# ── Scope: self (backward compatibility) ──────────────────────────────


class TestScopeSelfBackwardCompat:
    def test_uses_telemetry_directly(self):
        """Default scope=self reads from telemetry as before."""
        engine = DecisionEngine(rules=[_make_rule(scope="self", threshold=25.0)])
        telemetry = _make_telemetry(temp=30.0)
        cmds = engine.evaluate(telemetry)
        assert len(cmds) == 1
        assert cmds[0].device_id == "esp32-test"

    def test_no_scope_field_defaults_to_self(self):
        """Rules without explicit scope default to 'self'."""
        rule = _make_rule(threshold=25.0)
        assert rule.condition.scope == "self"
        assert rule.action.target_scope == "self"


# ── Target scope: all ─────────────────────────────────────────────────


class TestTargetScopeAll:
    def test_sends_to_all_devices_with_actuator(self):
        """target_scope: all generates one command per device with the actuator."""
        engine = DecisionEngine(rules=[
            _make_rule(threshold=25.0, target_scope="all"),
        ])
        coord = MockCoordinator(
            device_readings={},
            online_devices=["a", "b", "c"],
            device_actuators={"a": ["relay1"], "b": ["relay1"], "c": ["relay2"]},
            device_locations={"a": "r1", "b": "r2", "c": "r3"},
        )
        telemetry = _make_telemetry(temp=30.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 2
        target_devices = {cmd.device_id for cmd in cmds}
        assert target_devices == {"a", "b"}

    def test_skips_devices_without_actuator(self):
        """Devices without the target actuator are excluded."""
        engine = DecisionEngine(rules=[
            _make_rule(threshold=25.0, target_scope="all"),
        ])
        coord = MockCoordinator(
            device_readings={},
            online_devices=["a"],
            device_actuators={"a": ["relay2"]},  # no relay1
        )
        telemetry = _make_telemetry(temp=30.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 0


# ── Target scope: specific device_id ──────────────────────────────────


class TestTargetScopeSpecific:
    def test_sends_to_named_device(self):
        """target_scope: <device_id> sends to that device."""
        engine = DecisionEngine(rules=[
            _make_rule(threshold=25.0, target_scope="esp32-hallway"),
        ])
        coord = MockCoordinator(
            device_readings={},
            online_devices=["esp32-hallway"],
            device_locations={"esp32-hallway": "hallway"},
        )
        telemetry = _make_telemetry(temp=30.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 1
        assert cmds[0].device_id == "esp32-hallway"
        assert cmds[0].location == "hallway"

    def test_offline_device(self):
        """target_scope to an offline device produces no command."""
        engine = DecisionEngine(rules=[
            _make_rule(threshold=25.0, target_scope="esp32-hallway"),
        ])
        coord = MockCoordinator(
            device_readings={},
            online_devices=[],  # hallway is offline
            device_locations={"esp32-hallway": "hallway"},
        )
        telemetry = _make_telemetry(temp=30.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 0


# ── Target scope: self (backward compatibility) ───────────────────────


class TestTargetScopeSelfBackwardCompat:
    def test_sends_to_triggering_device(self):
        """Default target_scope=self sends command to the telemetry source."""
        engine = DecisionEngine(rules=[_make_rule(threshold=25.0)])
        telemetry = _make_telemetry(temp=30.0, device_id="esp32-source")
        cmds = engine.evaluate(telemetry)
        assert len(cmds) == 1
        assert cmds[0].device_id == "esp32-source"


# ── Cross-device state tracking ───────────────────────────────────────


class TestCrossDeviceStateTracking:
    def test_cross_device_rule_uses_shared_state_key(self):
        """Cross-device rules use _cross: prefix for state tracking."""
        engine = DecisionEngine(rules=[
            _make_rule(scope="any", threshold=25.0, duration_seconds=10),
        ])
        coord = MockCoordinator(
            device_readings={"a": {"temp1": 30.0}},
            online_devices=["a"],
            device_actuators={"a": ["relay1"]},
        )
        telemetry = _make_telemetry(temp=22.0)
        engine.evaluate(telemetry, coordinator=coord)

        # State key should use _cross: prefix, not device_id
        assert "_cross:temp1:test_rule" in engine.sensor_states
        assert "esp32-test:temp1:test_rule" not in engine.sensor_states

    def test_cooldown_works(self):
        """Cooldown prevents duplicate cross-device commands."""
        engine = DecisionEngine(rules=[_make_rule(scope="any", threshold=25.0)])
        coord = MockCoordinator(
            device_readings={"a": {"temp1": 30.0}},
            online_devices=["a"],
            device_actuators={"a": ["relay1"]},
        )
        telemetry = _make_telemetry(temp=22.0)

        # First call triggers
        cmds1 = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds1) == 1

        # Immediate second call is blocked by cooldown
        cmds2 = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds2) == 0


# ── YAML loading ──────────────────────────────────────────────────────


class TestCrossDeviceYAMLLoading:
    def test_load_scope_and_target_scope_from_yaml(self):
        """scope and target_scope are parsed from YAML."""
        import tempfile
        import yaml

        yaml_content = {
            "rules": [
                {
                    "name": "cross_test",
                    "description": "test",
                    "condition": {
                        "sensor": "temp1",
                        "operator": ">",
                        "threshold": 30,
                        "scope": "any",
                    },
                    "action": {
                        "target": "relay1",
                        "action": "set",
                        "value": True,
                        "reason": "test",
                        "target_scope": "all",
                    },
                }
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_content, f)
            path = f.name

        try:
            engine = DecisionEngine.from_yaml(path)
            assert len(engine.rules) == 1
            assert engine.rules[0].condition.scope == "any"
            assert engine.rules[0].action.target_scope == "all"
        finally:
            os.unlink(path)

    def test_yaml_without_scope_defaults(self):
        """Rules without scope fields get default 'self' values."""
        import tempfile
        import yaml

        yaml_content = {
            "rules": [
                {
                    "name": "basic_rule",
                    "description": "test",
                    "condition": {
                        "sensor": "temp1",
                        "operator": ">",
                        "threshold": 25,
                    },
                    "action": {
                        "target": "relay1",
                        "action": "set",
                        "value": True,
                        "reason": "test",
                    },
                }
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_content, f)
            path = f.name

        try:
            engine = DecisionEngine.from_yaml(path)
            assert engine.rules[0].condition.scope == "self"
            assert engine.rules[0].action.target_scope == "self"
        finally:
            os.unlink(path)


# ── Combined scope + target_scope ─────────────────────────────────────


class TestCombinedScopeAndTargetScope:
    def test_scope_any_target_scope_all(self):
        """scope: any + target_scope: all — read from any, send to all."""
        engine = DecisionEngine(rules=[
            _make_rule(scope="any", threshold=25.0, target_scope="all"),
        ])
        coord = MockCoordinator(
            device_readings={"a": {"temp1": 30.0}, "b": {"temp1": 20.0}},
            online_devices=["a", "b", "c"],
            device_actuators={"a": ["relay1"], "b": ["relay1"], "c": ["relay1"]},
            device_locations={"a": "r1", "b": "r2", "c": "r3"},
        )
        telemetry = _make_telemetry(temp=22.0)
        cmds = engine.evaluate(telemetry, coordinator=coord)
        # "a" has temp 30 > 25 (any matches), commands sent to a, b, c (all have relay1)
        assert len(cmds) == 3

    def test_scope_specific_target_scope_specific(self):
        """scope: <device_a> + target_scope: <device_b> — cross-device routing."""
        engine = DecisionEngine(rules=[
            _make_rule(
                scope="esp32-garage",
                threshold=35.0,
                target_scope="esp32-hallway",
            ),
        ])
        coord = MockCoordinator(
            device_readings={"esp32-garage": {"temp1": 40.0}},
            online_devices=["esp32-garage", "esp32-hallway"],
            device_locations={"esp32-garage": "garage", "esp32-hallway": "hallway"},
        )
        telemetry = _make_telemetry(temp=22.0, device_id="esp32-test")
        cmds = engine.evaluate(telemetry, coordinator=coord)
        assert len(cmds) == 1
        assert cmds[0].device_id == "esp32-hallway"
        assert cmds[0].location == "hallway"
