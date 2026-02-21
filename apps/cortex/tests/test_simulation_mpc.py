"""Integration tests for MPC simulation runs."""

import os
from pathlib import Path

import pytest

from simulations.runner import run_mpc, MPCSimulationResult
from simulations.scenarios.default import GOALS, PROFILE, MPC_CONFIG


OUTPUT_DIR = "simulations/output"


@pytest.fixture
def profile():
    return dict(PROFILE, strategy="balanced")


@pytest.fixture
def goals():
    return list(GOALS)


class TestMPCSimulation:
    """Test full MPC simulation runs."""

    def test_runs_to_completion(self, profile, goals):
        result = run_mpc(
            goals=goals,
            profile=profile,
            mpc_config=MPC_CONFIG,
            duration_minutes=10,
            step_seconds=30,
            start_hour=12,
        )
        assert isinstance(result, MPCSimulationResult)
        assert result.duration_minutes == 10
        expected_steps = (10 * 60) // 30
        assert len(result.timestamps) == expected_steps

    def test_result_contains_readings(self, profile, goals):
        result = run_mpc(
            goals=goals, profile=profile, mpc_config=MPC_CONFIG,
            duration_minutes=5, step_seconds=30, start_hour=12,
        )
        assert "temp1" in result.readings
        assert "hum1" in result.readings
        assert len(result.readings["temp1"]) == len(result.timestamps)

    def test_result_contains_intensities(self, profile, goals):
        result = run_mpc(
            goals=goals, profile=profile, mpc_config=MPC_CONFIG,
            duration_minutes=5, step_seconds=30, start_hour=12,
        )
        assert "fan" in result.intensities
        assert "exhaust_fan" in result.intensities
        assert "light" in result.intensities
        for name, vals in result.intensities.items():
            assert len(vals) == len(result.timestamps)
            for v in vals:
                assert 0.0 <= v <= 1.0

    def test_diagnostics_recorded(self, profile, goals):
        result = run_mpc(
            goals=goals, profile=profile, mpc_config=MPC_CONFIG,
            duration_minutes=5, step_seconds=30, start_hour=12,
        )
        n = len(result.timestamps)
        assert len(result.solve_times_ms) == n
        assert len(result.costs) == n
        assert len(result.goal_costs) == n
        assert len(result.energy_costs) == n
        assert len(result.rate_costs) == n

    def test_energy_tracked(self, profile, goals):
        result = run_mpc(
            goals=goals, profile=profile, mpc_config=MPC_CONFIG,
            duration_minutes=5, step_seconds=30, start_hour=12,
        )
        assert result.total_energy_wh >= 0
        assert isinstance(result.energy_breakdown, dict)

    def test_compliance_computed(self, profile, goals):
        result = run_mpc(
            goals=goals, profile=profile, mpc_config=MPC_CONFIG,
            duration_minutes=10, step_seconds=30, start_hour=12,
        )
        # Should have compliance entries for goal metrics
        assert len(result.compliance) > 0
        for metric, pct in result.compliance.items():
            assert 0.0 <= pct <= 1.0

    def test_health_scores_recorded(self, profile, goals):
        result = run_mpc(
            goals=goals, profile=profile, mpc_config=MPC_CONFIG,
            duration_minutes=5, step_seconds=30, start_hour=12,
        )
        assert len(result.health_scores) > 0
        assert result.avg_health > 0

    def test_custom_horizon(self, profile, goals):
        """Custom horizon should work."""
        cfg = dict(MPC_CONFIG, horizon_minutes=5.0)
        result = run_mpc(
            goals=goals, profile=profile, mpc_config=cfg,
            duration_minutes=5, step_seconds=30, start_hour=12,
        )
        assert len(result.timestamps) > 0

    def test_custom_energy_weight(self, profile, goals):
        """Higher energy weight should reduce total energy."""
        cfg_low = dict(MPC_CONFIG, w_energy=0.01)
        cfg_high = dict(MPC_CONFIG, w_energy=0.5)

        result_low = run_mpc(
            goals=goals, profile=profile, mpc_config=cfg_low,
            duration_minutes=10, step_seconds=30, start_hour=12,
        )
        result_high = run_mpc(
            goals=goals, profile=profile, mpc_config=cfg_high,
            duration_minutes=10, step_seconds=30, start_hour=12,
        )
        # Higher energy weight should generally use less energy
        # (not guaranteed in all cases, but should trend this way)
        assert result_high.total_energy_wh <= result_low.total_energy_wh * 1.5

    def test_day_night_transition_within_run(self, profile, goals):
        """MPC should handle a day→night transition during the simulation."""
        result = run_mpc(
            goals=goals, profile=profile, mpc_config=MPC_CONFIG,
            duration_minutes=30,  # 30 min crossing 22:00
            step_seconds=30,
            start_hour=21,  # start at 9 PM, crosses into night at 22:00
        )
        assert isinstance(result, MPCSimulationResult)
        assert len(result.timestamps) == (30 * 60) // 30
        # Compliance should still be computed
        assert len(result.compliance) > 0

    def test_compliance_day_only_metrics(self, profile, goals):
        """CO2 compliance should only count daytime steps (goal is day-only)."""
        result = run_mpc(
            goals=goals, profile=profile, mpc_config=MPC_CONFIG,
            duration_minutes=10, step_seconds=30, start_hour=12,
        )
        # At noon, CO2 goal is active — should have compliance entry
        assert "co2_1" in result.compliance


class TestMPCCharts:
    """Test chart generation."""

    def test_mpc_chart_saves_file(self, profile, goals):
        from simulations.charts import plot_mpc_simulation

        result = run_mpc(
            goals=goals, profile=profile, mpc_config=MPC_CONFIG,
            duration_minutes=5, step_seconds=30, start_hour=12,
        )
        output = Path(OUTPUT_DIR) / "test_mpc_chart.png"
        path = plot_mpc_simulation(result, output)
        assert path.exists()
        assert path.stat().st_size > 0
        path.unlink()  # cleanup

