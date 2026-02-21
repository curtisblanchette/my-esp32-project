"""Tests for the production MPC controller."""

from unittest.mock import MagicMock

import pytest

from simulations.room_config import default_room_config
from simulations.scenarios.default import GOALS
from src.services.mpc_controller import MPCController


@pytest.fixture
def mock_mqtt():
    mqtt = MagicMock()
    mqtt.publish_command = MagicMock(return_value="corr-001")
    return mqtt


@pytest.fixture
def controller(mock_mqtt):
    room = default_room_config()
    return MPCController(
        room_config=room,
        goals=GOALS,
        mqtt_client=mock_mqtt,
        location="room1",
        device_id="esp32-test",
    )


@pytest.fixture
def sample_readings():
    return {
        "temp1": 26.0,
        "hum1": 55.0,
        "light1": 500.0,
        "co2_1": 800.0,
        "soil1": 45.0,
        "soil2": 44.0,
        "soil3": 46.0,
        "soil4": 43.0,
        "leaf_temp1": 25.0,
    }


class TestMPCController:
    """Production MPC controller tests."""

    def test_on_telemetry_produces_commands(self, controller, sample_readings, mock_mqtt):
        """MPC solve returns command list and publishes via MQTT."""
        commands = controller.on_telemetry("esp32-test", sample_readings, 14.0)
        assert len(commands) > 0
        # MQTT publish called for each command
        assert mock_mqtt.publish_command.call_count == len(commands)

    def test_command_intensities_in_range(self, controller, sample_readings):
        """All command values are float intensities between 0.0 and 1.0."""
        commands = controller.on_telemetry("esp32-test", sample_readings, 14.0)
        for cmd in commands:
            assert isinstance(cmd.value, float), f"{cmd.target} value is {type(cmd.value)}"
            assert 0.0 <= cmd.value <= 1.0, f"{cmd.target} intensity {cmd.value} out of range"

    def test_calibrate_syncs_state(self, controller, sample_readings):
        """PhysicsEngine state matches readings after calibrate."""
        controller.physics.calibrate(sample_readings)
        assert controller.physics.temperature == 26.0
        assert controller.physics.humidity == 55.0
        assert controller.physics.co2 == 800.0
        assert controller.physics.light_intensity == 500.0
        assert controller.physics.soil_moisture[0] == 45.0
        assert controller.physics.soil_moisture[3] == 43.0

    def test_reload_goals(self, controller):
        """Reload goals updates the planner's goal list."""
        new_goals = [
            {"metric": "temp1", "rangeMin": 20.0, "rangeMax": 25.0, "priority": 2.0},
        ]
        controller.reload_goals(new_goals)
        assert len(controller.planner._goals) == 1
        assert controller.planner._goals[0].range_min == 20.0
        assert controller.planner._goals[0].weight == 2.0

    def test_mpc_result_fields(self, controller, sample_readings):
        """MPC commands have expected fields."""
        commands = controller.on_telemetry("esp32-test", sample_readings, 14.0)
        for cmd in commands:
            assert cmd.device_id == "esp32-test"
            assert cmd.location == "room1"
            assert cmd.action == "set"
            assert "MPC optimal" in cmd.reason

    def test_no_mqtt_does_not_crash(self, sample_readings):
        """Controller works without MQTT (commands returned but not published)."""
        room = default_room_config()
        controller = MPCController(
            room_config=room,
            goals=GOALS,
            mqtt_client=None,
            location="room1",
            device_id="esp32-test",
        )
        commands = controller.on_telemetry("esp32-test", sample_readings, 14.0)
        assert len(commands) > 0
