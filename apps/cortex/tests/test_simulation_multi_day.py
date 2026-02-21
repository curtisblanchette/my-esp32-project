"""Tests for multi-day MPC simulation with periodic compliance checkpoints.

Tests ambient schedule, MPC multi-day runner, and chart output.
"""

import math
import os
from pathlib import Path

import pytest

from simulations.physics import PhysicsEngine, default_ambient_schedule
from simulations.runner import (
    run_multi_day,
    MultiDayResult,
    PhaseResult,
    _compute_goal_compliance,
)
from simulations.charts import plot_multi_day
from simulations.scenarios.default import GOALS, PROFILE


# ── Ambient Schedule ──────────────────────────────────────────────────


class TestAmbientSchedule:
    """Tests for the day/night sinusoidal ambient temperature schedule."""

    def test_peak_at_2pm(self):
        """Peak temperature occurs at 14:00 (2pm)."""
        peak = default_ambient_schedule(14.0)
        assert peak == pytest.approx(32.0, abs=0.1)

    def test_trough_at_2am(self):
        """Trough temperature occurs at 02:00 (2am)."""
        trough = default_ambient_schedule(2.0)
        assert trough == pytest.approx(20.0, abs=0.1)

    def test_mean_temperature(self):
        """Average across 24 hours should be ~26°C."""
        temps = [default_ambient_schedule(h) for h in range(24)]
        avg = sum(temps) / len(temps)
        assert avg == pytest.approx(26.0, abs=0.5)

    def test_morning_rising(self):
        """Temperature rises from morning to afternoon."""
        t_8am = default_ambient_schedule(8.0)
        t_noon = default_ambient_schedule(12.0)
        t_2pm = default_ambient_schedule(14.0)
        assert t_8am < t_noon < t_2pm

    def test_evening_falling(self):
        """Temperature falls from afternoon to night."""
        t_2pm = default_ambient_schedule(14.0)
        t_8pm = default_ambient_schedule(20.0)
        t_midnight = default_ambient_schedule(0.0)
        assert t_2pm > t_8pm > t_midnight

    def test_schedule_range(self):
        """All values within expected range."""
        for h in range(24):
            for m in (0, 15, 30, 45):
                hour = h + m / 60.0
                temp = default_ambient_schedule(hour)
                assert 19.5 <= temp <= 32.5, f"Out of range at hour {hour}: {temp}"


class TestEnvironmentAmbientSchedule:
    """Tests that the environment correctly uses the ambient schedule."""

    def test_environment_uses_schedule(self):
        """Environment applies ambient_schedule when current_hour is provided."""
        env = PhysicsEngine(
            temperature=20.0,
            noise=False,
            ambient_schedule=lambda h: 35.0,  # constant high ambient
        )
        for _ in range(100):
            env.step(30.0, current_hour=14.0)
        # Temperature should drift toward 35°C
        assert env.temperature > 25.0

    def test_environment_ignores_schedule_without_hour(self):
        """ambient_schedule is ignored when current_hour is not provided."""
        env = PhysicsEngine(
            temperature=24.0,
            ambient_temp=30.0,
            noise=False,
            ambient_schedule=lambda h: 10.0,  # would pull low if used
        )
        for _ in range(100):
            env.step(30.0)  # no current_hour
        # Should drift toward ambient_temp=30, not schedule=10
        assert env.temperature > 25.0

    def test_environment_without_schedule(self):
        """Without ambient_schedule, uses static ambient_temp."""
        env = PhysicsEngine(
            temperature=24.0,
            ambient_temp=30.0,
            noise=False,
        )
        for _ in range(100):
            env.step(30.0, current_hour=14.0)
        # Should drift toward 30
        assert env.temperature > 25.0


# ── Multi-Day MPC Runner ─────────────────────────────────────────────


class TestMultiDayMPC:
    """Tests for the MPC-based multi-day simulation runner."""

    def test_multi_day_mpc_runs(self):
        """run_multi_day returns a valid MultiDayResult with phases."""
        result = run_multi_day(
            goals=GOALS,
            profile=PROFILE,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert isinstance(result, MultiDayResult)
        assert result.num_checkpoints == 2  # 60 / 30 = 2 phases
        assert len(result.phases) == 2

    def test_multi_day_phases_have_compliance(self):
        """Each phase has compliance dict with expected metrics."""
        result = run_multi_day(
            goals=GOALS,
            profile=PROFILE,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        for phase in result.phases:
            assert isinstance(phase.compliance, dict)
            assert len(phase.compliance) > 0
            # All compliance values between 0 and 1
            for metric, val in phase.compliance.items():
                assert 0.0 <= val <= 1.0, f"{metric} compliance {val} out of range"
            # avg_compliance should be reasonable
            assert 0.0 <= phase.avg_compliance <= 1.0

    def test_multi_day_intensities_continuous(self):
        """Intensities are 0.0-1.0 floats (not binary)."""
        result = run_multi_day(
            goals=GOALS,
            profile=PROFILE,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert len(result.intensities) > 0
        for name, values in result.intensities.items():
            assert len(values) == len(result.timestamps)
            for v in values:
                assert 0.0 <= v <= 1.0, f"{name} intensity {v} out of [0, 1]"

    def test_multi_day_energy_tracking(self):
        """Energy tracking produces non-negative values with a breakdown."""
        result = run_multi_day(
            goals=GOALS,
            profile=PROFILE,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert result.total_energy_wh >= 0
        assert len(result.energy_breakdown) > 0
        # Per-phase energy should be non-negative
        for phase in result.phases:
            assert phase.energy_wh >= 0

    def test_multi_day_mpc_diagnostics(self):
        """MPC diagnostics lists are populated with per-step data."""
        result = run_multi_day(
            goals=GOALS,
            profile=PROFILE,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        n_steps = len(result.timestamps)
        assert len(result.solve_times_ms) == n_steps
        assert len(result.costs) == n_steps
        assert len(result.goal_costs) == n_steps
        assert len(result.energy_costs) == n_steps
        assert len(result.rate_costs) == n_steps
        # Solve times should be positive
        assert all(t > 0 for t in result.solve_times_ms)

    def test_multi_day_timestamps_continuous(self):
        """Timestamps form a continuous, monotonically increasing series."""
        result = run_multi_day(
            goals=GOALS,
            profile=PROFILE,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert len(result.timestamps) == 120  # 60 * 60 / 30
        for i in range(1, len(result.timestamps)):
            assert result.timestamps[i] > result.timestamps[i - 1]

    def test_multi_day_readings_populated(self):
        """All sensor readings are populated across the full timeline."""
        result = run_multi_day(
            goals=GOALS,
            profile=PROFILE,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        expected_len = len(result.timestamps)
        assert len(result.readings["temp1"]) == expected_len
        assert len(result.readings["hum1"]) == expected_len

    def test_multi_day_effectiveness_trajectory(self):
        """Effectiveness trajectory has one entry per phase (avg_compliance)."""
        result = run_multi_day(
            goals=GOALS,
            profile=PROFILE,
            total_duration_minutes=90,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert len(result.effectiveness_trajectory) == len(result.phases)
        for val in result.effectiveness_trajectory:
            assert 0.0 <= val <= 1.0

    def test_multi_day_phase_structure(self):
        """Phases have correct boundaries and numbering."""
        result = run_multi_day(
            goals=GOALS,
            profile=PROFILE,
            total_duration_minutes=90,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert len(result.phases) == 3
        assert result.phases[0].phase_num == 0
        assert result.phases[0].start_minutes == 0.0
        assert result.phases[1].phase_num == 1
        assert result.phases[1].start_minutes > 0
        assert result.phases[2].phase_num == 2


# ── Compliance Helper ─────────────────────────────────────────────────


class TestComputeGoalCompliance:
    """Tests for the _compute_goal_compliance helper."""

    def test_perfect_compliance(self):
        """All readings in range gives 100% compliance."""
        readings = {"temp1": [25.0, 26.0, 27.0]}
        goals = [{"metric": "temp1", "rangeMin": 24.0, "rangeMax": 28.0}]
        result = _compute_goal_compliance(readings, goals, 8.0, 30)
        assert result["temp1"] == pytest.approx(1.0)

    def test_zero_compliance(self):
        """All readings out of range gives 0% compliance."""
        readings = {"temp1": [30.0, 31.0, 32.0]}
        goals = [{"metric": "temp1", "rangeMin": 24.0, "rangeMax": 28.0}]
        result = _compute_goal_compliance(readings, goals, 8.0, 30)
        assert result["temp1"] == pytest.approx(0.0)

    def test_partial_compliance(self):
        """Mix of in-range and out-of-range readings."""
        readings = {"temp1": [25.0, 30.0, 26.0, 31.0]}
        goals = [{"metric": "temp1", "rangeMin": 24.0, "rangeMax": 28.0}]
        result = _compute_goal_compliance(readings, goals, 8.0, 30)
        assert result["temp1"] == pytest.approx(0.5)

    def test_slice_compliance(self):
        """Compliance computed for a sub-slice of readings."""
        readings = {"temp1": [30.0, 30.0, 25.0, 26.0]}
        goals = [{"metric": "temp1", "rangeMin": 24.0, "rangeMax": 28.0}]
        # Only check indices 2-4 (the in-range readings)
        result = _compute_goal_compliance(readings, goals, 8.0, 30, start_idx=2, end_idx=4)
        assert result["temp1"] == pytest.approx(1.0)


# ── Multi-Day Chart ──────────────────────────────────────────────────


class TestMultiDayChart:
    """Tests for the multi-day chart generation."""

    def test_chart_generates_png(self, tmp_path):
        """plot_multi_day produces a valid PNG file."""
        result = run_multi_day(
            goals=GOALS,
            profile=PROFILE,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        output = tmp_path / "test_multi_day.png"
        path = plot_multi_day(result, output, goals=GOALS)
        assert path.exists()
        assert path.stat().st_size > 10000  # reasonable PNG size

    def test_chart_with_many_phases(self, tmp_path):
        """Chart handles more than 2 phases."""
        result = run_multi_day(
            goals=GOALS,
            profile=PROFILE,
            total_duration_minutes=120,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        output = tmp_path / "test_multi_day_4phase.png"
        path = plot_multi_day(result, output, goals=GOALS)
        assert path.exists()
        assert path.stat().st_size > 10000
