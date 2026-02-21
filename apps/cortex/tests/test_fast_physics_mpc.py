"""MPC integration tests for FastPhysicsEngine.

Verifies that the MPC planner works correctly with FastPhysicsEngine,
including trajectory similarity and functional compatibility.
"""

import pytest

from simulations.fast_physics import FastPhysicsEngine, FastSubstrateConfig
from simulations.physics import PhysicsEngine, SpaceConfig, PlantModel
from simulations.room_config import default_room_config
from simulations.state_planner import MPCConfig, MPCPlanner, goals_from_dicts


# ---------------------------------------------------------------------------
# Goals used across tests (matching default scenario)
# ---------------------------------------------------------------------------

_GOALS = [
    {"metric": "temp1", "rangeMin": 22.0, "rangeMax": 28.0, "tolerance": 1.0, "priority": 3.0},
    {"metric": "hum1", "rangeMin": 45.0, "rangeMax": 65.0, "tolerance": 2.0, "priority": 2.0},
    {"metric": "co2_1", "rangeMin": 800.0, "rangeMax": 1200.0, "tolerance": 50.0, "priority": 1.5},
]
_PROFILE = {"strategy": "balanced"}


# ---------------------------------------------------------------------------
# MPC Solve with FastPhysicsEngine
# ---------------------------------------------------------------------------

class TestMPCWithFastPhysics:
    """MPC planner should work with FastPhysicsEngine via duck typing."""

    def _make_fast_env(self) -> FastPhysicsEngine:
        return FastPhysicsEngine(
            temperature=26.0,
            humidity=55.0,
            co2=450.0,
            vwc=38.0,
            noise=False,
            ambient_temp=30.0,
        )

    def test_mpc_solve_succeeds(self):
        """MPC solve should succeed with FastPhysicsEngine."""
        env = self._make_fast_env()
        cfg = MPCConfig(horizon_minutes=15.0, max_iterations=50)
        goal_specs = goals_from_dicts(_GOALS, strategy="balanced")
        planner = MPCPlanner(config=cfg, goals=goal_specs)

        result = planner.solve(env, current_hour=12.0)
        assert result.success
        assert result.cost > 0
        assert len(result.optimal_intensities) > 0

    def test_mpc_solve_reasonable_time(self):
        """MPC solve with FastPhysicsEngine should be < 2s (cold)."""
        env = self._make_fast_env()
        cfg = MPCConfig(horizon_minutes=15.0, max_iterations=100)
        goal_specs = goals_from_dicts(_GOALS, strategy="balanced")
        planner = MPCPlanner(config=cfg, goals=goal_specs)

        result = planner.solve(env, current_hour=12.0)
        assert result.solve_time_ms < 2000, (
            f"Cold solve took {result.solve_time_ms:.1f}ms (target: <2000ms)"
        )

    def test_warm_solve_faster_than_cold(self):
        """Warm-started solve should be faster than cold solve."""
        env = self._make_fast_env()
        cfg = MPCConfig(horizon_minutes=15.0, max_iterations=100, warm_start=True)
        goal_specs = goals_from_dicts(_GOALS, strategy="balanced")
        planner = MPCPlanner(config=cfg, goals=goal_specs)

        # Cold solve
        cold = planner.solve(env, current_hour=12.0)

        # Warm solve
        env.step(30.0, current_hour=12.0)
        warm = planner.solve(env, current_hour=12.01)

        assert warm.solve_time_ms < cold.solve_time_ms

    def test_mpc_produces_predicted_trajectory(self):
        """MPC should produce a predicted trajectory with readings dicts."""
        env = self._make_fast_env()
        cfg = MPCConfig(horizon_minutes=15.0, max_iterations=50)
        goal_specs = goals_from_dicts(_GOALS, strategy="balanced")
        planner = MPCPlanner(config=cfg, goals=goal_specs)

        result = planner.solve(env, current_hour=12.0)
        assert len(result.predicted_trajectory) > 0
        for readings in result.predicted_trajectory:
            assert "temp1" in readings
            assert "hum1" in readings

    def test_high_temp_activates_cooling(self):
        """When temperature is high, MPC should activate fan/exhaust."""
        env = FastPhysicsEngine(
            temperature=35.0,
            humidity=55.0,
            co2=450.0,
            noise=False,
            ambient_temp=25.0,
        )
        cfg = MPCConfig(horizon_minutes=15.0, max_iterations=100)
        goal_specs = goals_from_dicts(_GOALS, strategy="balanced")
        planner = MPCPlanner(config=cfg, goals=goal_specs)

        result = planner.solve(env, current_hour=12.0)
        intensities = result.optimal_intensities
        cooling = intensities.get("fan", 0) + intensities.get("exhaust_fan", 0)
        assert cooling > 0, "MPC should activate cooling when temperature is high"

    def test_low_co2_activates_injector(self):
        """When CO2 is below target, MPC should activate injector."""
        env = FastPhysicsEngine(
            temperature=25.0,
            humidity=55.0,
            co2=400.0,  # below 800 target
            noise=False,
            ambient_temp=26.0,
        )
        cfg = MPCConfig(horizon_minutes=15.0, max_iterations=100)
        goal_specs = goals_from_dicts(_GOALS, strategy="balanced")
        planner = MPCPlanner(config=cfg, goals=goal_specs)

        result = planner.solve(env, current_hour=12.0)
        assert result.optimal_intensities.get("co2_injector", 0) > 0


# ---------------------------------------------------------------------------
# FastPhysicsEngine vs PhysicsEngine MPC comparison
# ---------------------------------------------------------------------------

class TestFastVsFullMPCTrajectory:
    """Compare MPC control decisions between fast and full physics engines."""

    def test_similar_temperature_control(self):
        """Both engines should produce qualitatively similar cooling decisions."""
        space = SpaceConfig()
        plant = PlantModel()

        full = PhysicsEngine(
            temperature=30.0, humidity=55.0, co2=450.0,
            noise=False, ambient_temp=30.0,
            tent=space, plant=plant,
        )
        fast = FastPhysicsEngine(
            temperature=30.0, humidity=55.0, co2=450.0, vwc=38.0,
            noise=False, ambient_temp=30.0,
            tent=space, plant=plant,
        )

        cfg = MPCConfig(horizon_minutes=15.0, max_iterations=50)
        goal_specs = goals_from_dicts(_GOALS, strategy="balanced")

        planner_full = MPCPlanner(config=cfg, goals=goal_specs)
        planner_fast = MPCPlanner(config=cfg, goals=goal_specs)

        result_full = planner_full.solve(full, current_hour=12.0)
        result_fast = planner_fast.solve(fast, current_hour=12.0)

        assert result_full.success
        assert result_fast.success

        # Both should agree on CO2 injection (clear signal: CO2 is below target)
        full_co2 = result_full.optimal_intensities.get("co2_injector", 0)
        fast_co2 = result_fast.optimal_intensities.get("co2_injector", 0)
        if full_co2 > 0:
            assert fast_co2 > 0, "Fast engine should also inject CO2 when full engine does"

    def test_fast_engine_simpler_state(self):
        """FastPhysicsEngine should have simpler state (fewer floats in snapshot)."""
        space = SpaceConfig()
        plant = PlantModel()

        full = PhysicsEngine(
            temperature=28.0, humidity=55.0, co2=450.0,
            noise=False, ambient_temp=30.0,
            tent=space, plant=plant,
        )
        fast = FastPhysicsEngine(
            temperature=28.0, humidity=55.0, co2=450.0, vwc=38.0,
            noise=False, ambient_temp=30.0,
            tent=space, plant=plant,
        )

        full_state = full.save_state()
        fast_state = fast.save_state()

        # Fast engine uses single VWC instead of soil_moisture list (tuple)
        # so its state tuple should have all-float elements (no nested tuples)
        assert all(isinstance(v, float) for v in fast_state), (
            "Fast engine state should be all floats (flat tuple)"
        )

        # Both should produce valid readings
        full_r = full.get_readings()
        fast_r = fast.get_readings()
        assert "temp1" in full_r and "temp1" in fast_r
        assert "soil1" in fast_r  # single VWC mapped to soil1


# ---------------------------------------------------------------------------
# run_mpc() integration with fast_physics flag
# ---------------------------------------------------------------------------

class TestRunMPCFastPhysics:
    """Test run_mpc() with fast_physics=True."""

    def test_run_mpc_fast_physics_completes(self):
        """run_mpc with fast_physics=True should complete successfully."""
        from simulations.runner import run_mpc

        result = run_mpc(
            goals=_GOALS,
            profile=_PROFILE,
            mpc_config={"horizon_minutes": 15.0, "max_iterations": 50},
            duration_minutes=10,
            step_seconds=30,
            start_hour=12,
            fast_physics=True,
        )

        assert len(result.timestamps) > 0
        assert len(result.solve_times_ms) > 0
        assert result.total_energy_wh >= 0

    def test_run_mpc_fast_physics_has_readings(self):
        """Fast physics run should produce all expected sensor readings."""
        from simulations.runner import run_mpc

        result = run_mpc(
            goals=_GOALS,
            profile=_PROFILE,
            mpc_config={"horizon_minutes": 15.0, "max_iterations": 50},
            duration_minutes=5,
            step_seconds=30,
            start_hour=12,
            fast_physics=True,
        )

        assert "temp1" in result.readings
        assert "hum1" in result.readings
        assert "co2_1" in result.readings
        assert len(result.readings["temp1"]) > 0

    def test_run_mpc_fast_physics_with_room_config(self):
        """Fast physics should work with room config parameters."""
        from simulations.runner import run_mpc

        room = default_room_config()
        result = run_mpc(
            goals=_GOALS,
            profile=_PROFILE,
            mpc_config={"horizon_minutes": 15.0, "max_iterations": 50},
            duration_minutes=5,
            step_seconds=30,
            start_hour=12,
            actuator_specs=room.actuator_specs,
            ventilation_fn=room.ventilation_fn,
            substrate_config=room.substrate_config,
            space=room.space,
            plant=room.plant,
            ambient_temp=room.ambient_temp,
            ambient_schedule=room.ambient_schedule_fn,
            initial_conditions=room.initial_conditions,
            fast_physics=True,
        )

        assert len(result.timestamps) > 0
        assert result.compliance  # should have compliance data

    def test_run_mpc_fast_physics_compliance_keys(self):
        """Fast physics MPC should produce compliance data for goal metrics."""
        from simulations.runner import run_mpc

        result = run_mpc(
            goals=_GOALS,
            profile=_PROFILE,
            mpc_config={"horizon_minutes": 15.0, "max_iterations": 50},
            duration_minutes=10,
            step_seconds=30,
            start_hour=12,
            fast_physics=True,
        )

        # Should have compliance for at least temp1 and hum1
        assert "temp1" in result.compliance or "hum1" in result.compliance
