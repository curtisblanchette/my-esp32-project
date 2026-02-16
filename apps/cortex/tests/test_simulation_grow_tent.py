"""Tests for the grow tent simulation physics engine and runner."""

import os
from pathlib import Path

import pytest

from simulations.environment import GrowTentEnvironment, ACTUATOR_MAP
from simulations.runner import SimulationRunner
from simulations.charts import plot_simulation
from simulations.scenarios.grow_tent_rules import GROW_TENT_RULES


class TestGrowTentEnvironment:
    """Physics engine unit tests."""

    def test_initial_state(self):
        env = GrowTentEnvironment(noise=False)
        assert env.temperature == 24.0
        assert env.humidity == 55.0
        assert len(env.soil_moisture) == 4
        assert env.light_intensity == 0.0
        assert not env.fan
        assert not env.exhaust_fan

    def test_natural_drift_temp_rises_toward_ambient(self):
        """Temperature drifts toward ambient (30°C) without actuators."""
        env = GrowTentEnvironment(noise=False, ambient_temp=30.0)
        initial_temp = env.temperature
        for _ in range(60):  # 30 minutes at 30s steps
            env.step(30)
        assert env.temperature > initial_temp

    def test_natural_drift_soil_dries(self):
        """Soil moisture decreases naturally over time."""
        env = GrowTentEnvironment(noise=False)
        initial_soil = list(env.soil_moisture)
        for _ in range(60):
            env.step(30)
        for i in range(4):
            assert env.soil_moisture[i] < initial_soil[i]

    def test_fan_cools_temperature(self):
        """Fan ON reduces temperature compared to no fan."""
        env_no_fan = GrowTentEnvironment(noise=False, ambient_temp=30.0)
        env_fan = GrowTentEnvironment(noise=False, ambient_temp=30.0)
        env_fan.fan = True

        for _ in range(60):
            env_no_fan.step(30)
            env_fan.step(30)

        assert env_fan.temperature < env_no_fan.temperature

    def test_exhaust_cools_and_dehumidifies(self):
        """Exhaust fan reduces both temperature and humidity."""
        env = GrowTentEnvironment(noise=False, ambient_temp=30.0)
        env.temperature = 28.0
        env.humidity = 60.0
        env.exhaust_fan = True

        initial_temp = env.temperature
        initial_hum = env.humidity
        for _ in range(20):
            env.step(30)

        assert env.temperature < initial_temp
        assert env.humidity < initial_hum

    def test_irrigation_raises_soil_and_humidity(self):
        """Irrigation increases soil moisture and raises humidity."""
        env = GrowTentEnvironment(noise=False)
        env.soil_moisture = [30.0, 30.0, 30.0, 30.0]
        env.humidity = 45.0
        env.irrigation = True

        initial_hum = env.humidity
        initial_soil = list(env.soil_moisture)
        for _ in range(20):
            env.step(30)

        for i in range(4):
            assert env.soil_moisture[i] > initial_soil[i]
        assert env.humidity > initial_hum

    def test_humidifier_raises_humidity(self):
        env = GrowTentEnvironment(noise=False)
        env.humidity = 35.0
        env.humidifier = True
        for _ in range(20):
            env.step(30)
        assert env.humidity > 35.0

    def test_dehumidifier_lowers_humidity(self):
        env = GrowTentEnvironment(noise=False)
        env.humidity = 70.0
        env.dehumidifier = True
        for _ in range(20):
            env.step(30)
        assert env.humidity < 70.0

    def test_light_adds_heat(self):
        """Grow light ON increases temperature."""
        env_dark = GrowTentEnvironment(noise=False, ambient_temp=24.0)
        env_lit = GrowTentEnvironment(noise=False, ambient_temp=24.0)
        env_lit.light = True

        for _ in range(60):
            env_dark.step(30)
            env_lit.step(30)

        assert env_lit.temperature > env_dark.temperature
        assert env_lit.light_intensity == 800.0
        assert env_dark.light_intensity == 0.0

    def test_cross_variable_high_temp_dries_soil_faster(self):
        """Higher temperature accelerates soil moisture evaporation."""
        env_cool = GrowTentEnvironment(noise=False, ambient_temp=22.0)
        env_cool.temperature = 22.0

        env_hot = GrowTentEnvironment(noise=False, ambient_temp=35.0)
        env_hot.temperature = 35.0

        for _ in range(60):
            env_cool.step(30)
            env_hot.step(30)

        # Hot environment should have drier soil
        for i in range(4):
            assert env_hot.soil_moisture[i] < env_cool.soil_moisture[i]

    def test_clamping(self):
        """Values are clamped to valid ranges."""
        env = GrowTentEnvironment(noise=False)
        env.temperature = 50.0
        env.humidity = 100.0
        env.soil_moisture = [110.0, -5.0, 50.0, 50.0]
        env.step(0.001)
        assert env.temperature <= 45.0
        assert env.humidity <= 95.0
        assert env.soil_moisture[0] <= 100.0
        assert env.soil_moisture[1] >= 0.0

    def test_get_readings_keys(self):
        env = GrowTentEnvironment(noise=False)
        readings = env.get_readings()
        assert "temp1" in readings
        assert "hum1" in readings
        assert "light1" in readings
        assert all(f"soil{i+1}" in readings for i in range(4))

    def test_set_actuator_by_relay_id(self):
        env = GrowTentEnvironment(noise=False)
        env.set_actuator("relay1", True)
        assert env.fan is True
        env.set_actuator("relay2", True)
        assert env.exhaust_fan is True
        env.set_actuator("relay5", True)
        assert env.irrigation is True

    def test_set_actuator_by_name(self):
        env = GrowTentEnvironment(noise=False)
        env.set_actuator("fan", True)
        assert env.fan is True
        env.set_actuator("exhaust_fan", True)
        assert env.exhaust_fan is True

    def test_actuator_map_completeness(self):
        """All relay IDs map to valid actuator attributes."""
        env = GrowTentEnvironment(noise=False)
        for relay_id, attr_name in ACTUATOR_MAP.items():
            assert hasattr(env, attr_name), f"{relay_id} → {attr_name} not found"


class TestSimulationRunner:
    """Integration tests for the simulation runner with DecisionEngine."""

    def test_short_run_produces_results(self):
        """A short run produces timestamps, readings, and actuator states."""
        env = GrowTentEnvironment(noise=False)
        runner = SimulationRunner(
            environment=env,
            rules=GROW_TENT_RULES,
            duration_minutes=5,
            step_seconds=30,
            start_hour=12,
        )
        result = runner.run()

        assert len(result.timestamps) == 10  # 5min / 0.5min
        assert "temp1" in result.readings
        assert "hum1" in result.readings
        assert len(result.readings["temp1"]) == 10
        assert "fan" in result.actuators
        assert len(result.actuators["fan"]) == 10

    def test_full_run_produces_commands(self):
        """A longer run with warm ambient temp triggers temperature rules."""
        env = GrowTentEnvironment(noise=False, ambient_temp=32.0)
        runner = SimulationRunner(
            environment=env,
            rules=GROW_TENT_RULES,
            duration_minutes=60,
            step_seconds=30,
            start_hour=12,
        )
        result = runner.run()

        # Should trigger at least fan and/or exhaust
        assert len(result.events) > 0
        targets = {e.target for e in result.events}
        assert "relay1" in targets or "relay2" in targets

    def test_actuator_feedback_loop(self):
        """Rule fires → actuator ON → readings change → restore rule fires."""
        env = GrowTentEnvironment(noise=False, ambient_temp=32.0)
        env.temperature = 29.0  # start hot

        runner = SimulationRunner(
            environment=env,
            rules=GROW_TENT_RULES,
            duration_minutes=120,
            step_seconds=30,
            start_hour=12,
        )
        result = runner.run()

        # Should see both ON and OFF events for exhaust/fan
        on_events = [e for e in result.events if e.value is True]
        off_events = [e for e in result.events if e.value is False]
        assert len(on_events) > 0
        assert len(off_events) > 0

    def test_irrigation_cycle(self):
        """Dry soil triggers irrigation which then saturates and stops."""
        env = GrowTentEnvironment(noise=False, ambient_temp=30.0)
        env.soil_moisture = [38.0, 38.0, 38.0, 38.0]  # near threshold

        # Only include irrigation rules for focused test
        irrigation_rules = [
            r for r in GROW_TENT_RULES
            if "irrigat" in r["name"] or "soil" in r["name"]
        ]
        runner = SimulationRunner(
            environment=env,
            rules=irrigation_rules,
            duration_minutes=60,
            step_seconds=30,
            start_hour=12,
        )
        result = runner.run()

        irr_events = [e for e in result.events if e.target == "relay5"]
        assert len(irr_events) >= 2  # ON then OFF
        # First event should be irrigation ON
        assert irr_events[0].value is True
        # Should eventually turn off
        off_events = [e for e in irr_events if e.value is False]
        assert len(off_events) >= 1

    def test_context_builds_trends(self):
        """After enough steps, trend context is available for rules."""
        env = GrowTentEnvironment(noise=False, ambient_temp=30.0)
        runner = SimulationRunner(
            environment=env,
            rules=[],  # no rules, just test context building
            duration_minutes=5,
            step_seconds=30,
            start_hour=12,
        )
        result = runner.run()
        # Just verifying it runs without error and produces data
        assert len(result.timestamps) > 0

    def test_result_event_count(self):
        """Events list accurately reflects state changes, not redundant fires."""
        env = GrowTentEnvironment(noise=False, ambient_temp=30.0)
        runner = SimulationRunner(
            environment=env,
            rules=GROW_TENT_RULES,
            duration_minutes=30,
            step_seconds=30,
            start_hour=12,
        )
        result = runner.run()

        # Each event should be a state change — no duplicate consecutive ON/ON
        for target_id in set(e.target for e in result.events):
            target_events = [e for e in result.events if e.target == target_id]
            for i in range(1, len(target_events)):
                assert target_events[i].value != target_events[i - 1].value, (
                    f"Redundant command for {target_id} at "
                    f"{target_events[i].time_minutes}m"
                )


class TestChartOutput:
    """Tests for chart generation."""

    def test_chart_generates_png(self, tmp_path):
        """Chart generates a valid PNG file."""
        env = GrowTentEnvironment(noise=False)
        runner = SimulationRunner(
            environment=env,
            rules=GROW_TENT_RULES,
            duration_minutes=10,
            step_seconds=30,
            start_hour=12,
        )
        result = runner.run()

        output = tmp_path / "test_chart.png"
        returned_path = plot_simulation(result, output)

        assert returned_path == output
        assert output.exists()
        assert output.stat().st_size > 1000  # non-trivial file

    def test_chart_with_no_events(self, tmp_path):
        """Chart handles simulation with no rule-fired events."""
        env = GrowTentEnvironment(noise=False, ambient_temp=24.0)
        runner = SimulationRunner(
            environment=env,
            rules=[],
            duration_minutes=5,
            step_seconds=30,
            start_hour=12,
        )
        result = runner.run()

        output = tmp_path / "no_events.png"
        plot_simulation(result, output)
        assert output.exists()
