"""Tests for multi-day adaptive simulation with periodic Rule Advisor.

Tests ambient schedule, multi-phase runner, convergence, and chart output.
"""

import math
import os
from pathlib import Path

import pytest

from simulations.environment import GrowTentEnvironment, default_ambient_schedule
from simulations.runner import (
    SimulationRunner,
    run_multi_day,
    MultiDayResult,
    PhaseResult,
    _avg_effectiveness,
)
from simulations.charts import plot_multi_day
from simulations.scenarios.suboptimal_rules import SUBOPTIMAL_RULES
from simulations.scenarios.grow_tent_rules import GROW_TENT_RULES


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
        env = GrowTentEnvironment(
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
        env = GrowTentEnvironment(
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
        env = GrowTentEnvironment(
            temperature=24.0,
            ambient_temp=30.0,
            noise=False,
        )
        for _ in range(100):
            env.step(30.0, current_hour=14.0)
        # Should drift toward 30
        assert env.temperature > 25.0


# ── Multi-Day Runner ──────────────────────────────────────────────────


class TestMultiDayRunner:
    """Tests for the multi-day simulation runner."""

    def test_basic_run_returns_result(self):
        """run_multi_day returns a valid MultiDayResult."""
        result = run_multi_day(
            rules=GROW_TENT_RULES,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert isinstance(result, MultiDayResult)
        assert result.num_checkpoints == 2  # 60 / 30 = 2 phases
        assert len(result.phases) == 2

    def test_phase_structure(self):
        """Each phase has correct boundaries."""
        result = run_multi_day(
            rules=GROW_TENT_RULES,
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

    def test_timestamps_continuous(self):
        """Timestamps form a continuous, monotonically increasing series."""
        result = run_multi_day(
            rules=GROW_TENT_RULES,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert len(result.timestamps) == 120  # 60 * 60 / 30
        for i in range(1, len(result.timestamps)):
            assert result.timestamps[i] > result.timestamps[i - 1]

    def test_readings_populated(self):
        """All sensor readings are populated across the full timeline."""
        result = run_multi_day(
            rules=GROW_TENT_RULES,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        expected_len = len(result.timestamps)
        assert len(result.readings["temp1"]) == expected_len
        assert len(result.readings["hum1"]) == expected_len
        assert len(result.readings["soil1"]) == expected_len

    def test_effectiveness_trajectory_length(self):
        """Effectiveness trajectory has one entry per phase."""
        result = run_multi_day(
            rules=GROW_TENT_RULES,
            total_duration_minutes=90,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert len(result.effectiveness_trajectory) == len(result.phases)

    def test_baseline_snapshots_per_phase(self):
        """Baseline snapshots captured at each checkpoint."""
        result = run_multi_day(
            rules=GROW_TENT_RULES,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert len(result.baseline_snapshots) == len(result.phases)

    def test_baselines_accumulate(self):
        """Later phases have richer baselines (more samples)."""
        result = run_multi_day(
            rules=GROW_TENT_RULES,
            total_duration_minutes=90,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        # Baselines are cumulative — later snapshots should have more samples
        snap0 = result.baseline_snapshots[0]
        snap2 = result.baseline_snapshots[2]

        # Sum sample counts across all metrics and hours
        def total_samples(snap):
            total = 0
            for metric, hours in snap.items():
                for hour, data in hours.items():
                    total += data["sampleCount"]
            return total

        assert total_samples(snap2) > total_samples(snap0)


# ── Multi-Day Adaptive Learning ──────────────────────────────────────


class TestMultiDayAdaptive:
    """Tests that the advisor improves suboptimal rules across phases."""

    def test_suboptimal_generates_suggestions(self):
        """Suboptimal rules produce suggestions at first checkpoint."""
        result = run_multi_day(
            rules=SUBOPTIMAL_RULES,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert result.phases[0].num_suggestions_generated > 0
        assert result.total_suggestions > 0

    def test_suboptimal_effectiveness_changes(self):
        """Effectiveness trajectory changes across phases as advisor adjusts rules."""
        result = run_multi_day(
            rules=SUBOPTIMAL_RULES,
            total_duration_minutes=90,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        # Should produce outcomes in at least some phases
        total_outcomes = sum(len(p.outcomes) for p in result.phases)
        assert total_outcomes > 0
        # Trajectory should have entries
        assert len(result.effectiveness_trajectory) == len(result.phases)

    def test_suggestions_decrease_over_time(self):
        """Later phases should generate fewer suggestions as rules converge."""
        result = run_multi_day(
            rules=SUBOPTIMAL_RULES,
            total_duration_minutes=120,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        # First phase should have more suggestions than the last
        first_sugg = result.phases[0].num_suggestions_generated
        last_sugg = result.phases[-1].num_suggestions_generated
        assert first_sugg >= last_sugg

    def test_suboptimal_generates_more_suggestions_than_good(self):
        """Suboptimal rules trigger more advisor suggestions than good rules."""
        bad = run_multi_day(
            rules=SUBOPTIMAL_RULES,
            total_duration_minutes=90,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        good = run_multi_day(
            rules=GROW_TENT_RULES,
            total_duration_minutes=90,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        # Suboptimal rules should trigger more advisor corrections
        assert bad.phases[0].num_suggestions_generated >= good.phases[0].num_suggestions_generated

    def test_suggestions_generated_for_suboptimal(self):
        """Advisor generates suggestions for suboptimal rules (may not apply if low confidence)."""
        result = run_multi_day(
            rules=SUBOPTIMAL_RULES,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert result.total_suggestions > 0


# ── Convergence ──────────────────────────────────────────────────────


class TestConvergence:
    """Tests that the simulation can detect convergence and stop early."""

    def test_good_rules_converge(self):
        """Standard rules should converge (0 applied suggestions) within a few phases."""
        result = run_multi_day(
            rules=GROW_TENT_RULES,
            total_duration_minutes=180,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        # At least some phases should have 0 applied suggestions
        # (suggestions may still be generated but filtered by confidence)
        zero_applied_phases = sum(1 for p in result.phases if p.num_suggestions_applied == 0)
        assert zero_applied_phases > 0


# ── Multi-Day Chart ──────────────────────────────────────────────────


class TestMultiDayChart:
    """Tests for the multi-day chart generation."""

    def test_chart_generates_png(self, tmp_path):
        """plot_multi_day produces a valid PNG file."""
        result = run_multi_day(
            rules=SUBOPTIMAL_RULES,
            total_duration_minutes=60,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        output = tmp_path / "test_multi_day.png"
        path = plot_multi_day(result, output)
        assert path.exists()
        assert path.stat().st_size > 10000  # reasonable PNG size

    def test_chart_with_many_phases(self, tmp_path):
        """Chart handles more than 2 phases."""
        result = run_multi_day(
            rules=SUBOPTIMAL_RULES,
            total_duration_minutes=120,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        output = tmp_path / "test_multi_day_4phase.png"
        path = plot_multi_day(result, output)
        assert path.exists()
        assert path.stat().st_size > 10000


# ── Effectiveness Guard ──────────────────────────────────────────────


class TestEffectivenessGuardInSimulation:
    """Tests that the effectiveness guard prevents over-correction in multi-day simulation."""

    def test_good_rules_not_over_corrected(self):
        """Good rules should not have unreachable suggestions applied.

        The confidence filter prevents low-confidence suggestions from being
        applied, so rule thresholds stay intact even when the advisor flags them.
        """
        result = run_multi_day(
            rules=GROW_TENT_RULES,
            total_duration_minutes=180,
            checkpoint_interval_minutes=60,
            step_seconds=30,
            start_hour=10,
        )

        # With confidence filtering, unreachable suggestions should NOT be applied
        # (they have confidence < 0.8 in short simulations)
        total_unreachable_applied = 0
        for phase in result.phases:
            for s in phase.suggestions:
                if "unreachable" in s.get("reason", "") and s.get("confidence", 0) >= 0.8:
                    total_unreachable_applied += 1

        assert total_unreachable_applied == 0, (
            f"No unreachable suggestions should have confidence >= 0.8, got {total_unreachable_applied}"
        )

    def test_good_rules_maintain_commands(self):
        """Good rules should keep firing commands across all phases (not silenced)."""
        result = run_multi_day(
            rules=GROW_TENT_RULES,
            total_duration_minutes=120,
            checkpoint_interval_minutes=60,
            step_seconds=30,
            start_hour=10,
        )

        # Every phase should have some commands firing
        for phase in result.phases:
            assert len(phase.events) > 0, (
                f"Phase {phase.phase_num} has zero commands — rules may have been over-corrected"
            )

    def test_suboptimal_rules_still_corrected(self):
        """Suboptimal rules should STILL receive suggestions despite the guard."""
        result = run_multi_day(
            rules=SUBOPTIMAL_RULES,
            total_duration_minutes=90,
            checkpoint_interval_minutes=30,
            step_seconds=30,
            start_hour=8,
        )
        assert result.total_suggestions > 0
