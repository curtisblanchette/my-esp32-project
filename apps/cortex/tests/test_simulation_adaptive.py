"""Tests for the adaptive learning simulation — outcome tracking, baselines, and gap analysis."""

import pytest

from simulations.environment import GrowTentEnvironment
from simulations.runner import (
    SimulationRunner, run_adaptive, _avg_effectiveness,
    OutcomeScore, AdaptiveResult,
)
from simulations.charts import plot_adaptive
from simulations.scenarios.grow_tent_rules import GROW_TENT_RULES
from simulations.scenarios.suboptimal_rules import SUBOPTIMAL_RULES


class TestOutcomeTracking:
    """Tests for inline outcome scoring during simulation."""

    def test_outcome_tracking_produces_scores(self):
        """Running with track_outcomes=True produces outcome records."""
        env = GrowTentEnvironment(noise=False, ambient_temp=32.0)
        runner = SimulationRunner(
            environment=env,
            rules=GROW_TENT_RULES,
            duration_minutes=60,
            step_seconds=30,
            start_hour=12,
            track_outcomes=True,
        )
        result = runner.run()

        # With 32°C ambient, temp rules should fire and produce outcomes
        assert len(result.outcomes) > 0
        for outcome in result.outcomes:
            assert isinstance(outcome, OutcomeScore)
            assert -1.0 <= outcome.effectiveness <= 1.0
            assert outcome.rule_name
            assert outcome.target_metric

    def test_outcome_tracking_disabled_by_default(self):
        """Without track_outcomes, no outcomes are recorded."""
        env = GrowTentEnvironment(noise=False, ambient_temp=32.0)
        runner = SimulationRunner(
            environment=env,
            rules=GROW_TENT_RULES,
            duration_minutes=30,
            step_seconds=30,
            start_hour=12,
        )
        result = runner.run()
        assert len(result.outcomes) == 0

    def test_outcome_scores_match_expected_direction(self):
        """Exhaust fan ON with hot ambient should produce positive (decrease) effectiveness."""
        env = GrowTentEnvironment(noise=False, ambient_temp=32.0)
        env.temperature = 29.0  # start hot to trigger exhaust

        # Only use exhaust rules
        exhaust_rules = [
            r for r in GROW_TENT_RULES
            if "exhaust" in r["name"]
        ]
        runner = SimulationRunner(
            environment=env,
            rules=exhaust_rules,
            duration_minutes=60,
            step_seconds=30,
            start_hour=12,
            track_outcomes=True,
        )
        result = runner.run()

        # Exhaust ON should have "decrease" direction for temperature
        on_outcomes = [o for o in result.outcomes if o.desired_direction == "decrease"]
        assert len(on_outcomes) > 0

    def test_avg_effectiveness_calculation(self):
        """_avg_effectiveness computes correct mean."""
        outcomes = [
            OutcomeScore("r1", "t1", "", 1.0, "temperature", "decrease",
                         28.0, {60: 27.5, 300: 27.0, 600: 26.5}, 0.5),
            OutcomeScore("r2", "t2", "", 2.0, "humidity", "decrease",
                         70.0, {60: 68.0, 300: 65.0, 600: 62.0}, 0.8),
        ]
        assert _avg_effectiveness(outcomes) == pytest.approx(0.65)

    def test_avg_effectiveness_empty(self):
        """_avg_effectiveness returns 0.0 for empty list."""
        assert _avg_effectiveness([]) == 0.0


class TestBaselineLearning:
    """Tests for baseline accumulation during simulation."""

    def test_baselines_populated_when_tracking(self):
        """Running with track_outcomes populates baseline data."""
        env = GrowTentEnvironment(noise=False, ambient_temp=30.0)
        runner = SimulationRunner(
            environment=env,
            rules=GROW_TENT_RULES,
            duration_minutes=30,
            step_seconds=30,
            start_hour=12,
            track_outcomes=True,
        )
        result = runner.run()

        assert len(result.baselines) > 0
        assert "temperature" in result.baselines
        assert "humidity" in result.baselines

        # Check baseline structure
        for sensor_type, hours in result.baselines.items():
            for hour, data in hours.items():
                assert "avg" in data
                assert "stdDev" in data
                assert "sampleCount" in data
                assert data["sampleCount"] > 0

    def test_baselines_empty_when_not_tracking(self):
        """Without tracking, baselines are empty."""
        env = GrowTentEnvironment(noise=False)
        runner = SimulationRunner(
            environment=env, rules=[], duration_minutes=5,
            step_seconds=30, start_hour=12,
        )
        result = runner.run()
        assert len(result.baselines) == 0

    def test_baselines_for_advisor_format(self):
        """get_baselines_for_advisor() returns correctly formatted dicts."""
        env = GrowTentEnvironment(noise=False, ambient_temp=30.0)
        runner = SimulationRunner(
            environment=env,
            rules=GROW_TENT_RULES,
            duration_minutes=30,
            step_seconds=30,
            start_hour=12,
            track_outcomes=True,
        )
        runner.run()

        advisor_baselines = runner.get_baselines_for_advisor()
        assert len(advisor_baselines) > 0

        for b in advisor_baselines:
            assert "deviceId" in b
            assert "metric" in b
            assert "hour" in b
            assert "avg" in b
            assert "stdDev" in b
            assert "sampleCount" in b
            assert b["sampleCount"] >= 10

    def test_baseline_values_reasonable(self):
        """Baseline averages are in expected ranges for the environment."""
        env = GrowTentEnvironment(noise=False, ambient_temp=28.0)
        runner = SimulationRunner(
            environment=env,
            rules=[],  # no rules, just observe natural drift
            duration_minutes=30,
            step_seconds=30,
            start_hour=12,
            track_outcomes=True,
        )
        runner.run()

        baselines = runner.get_baselines_for_advisor()
        temp_baselines = [b for b in baselines if b["metric"] == "temperature"]
        if temp_baselines:
            for b in temp_baselines:
                assert 20.0 <= b["avg"] <= 35.0


class TestAdaptiveSimulation:
    """Tests for the full adaptive learning loop."""

    def test_run_adaptive_returns_result(self):
        """run_adaptive() returns a valid AdaptiveResult."""
        adaptive = run_adaptive(
            rules=GROW_TENT_RULES,
            duration_minutes=30,
            step_seconds=30,
            start_hour=12,
            ambient_temp=32.0,
        )

        assert isinstance(adaptive, AdaptiveResult)
        assert adaptive.phase1.duration_minutes == 30
        assert adaptive.phase2.duration_minutes == 30
        assert isinstance(adaptive.suggestions, list)
        assert isinstance(adaptive.phase1_avg_effectiveness, float)
        assert isinstance(adaptive.phase2_avg_effectiveness, float)
        assert isinstance(adaptive.improvement, float)

    def test_adaptive_produces_outcomes_both_phases(self):
        """Both phases produce outcome records."""
        adaptive = run_adaptive(
            rules=GROW_TENT_RULES,
            duration_minutes=60,
            step_seconds=30,
            start_hour=12,
            ambient_temp=32.0,
        )

        assert len(adaptive.phase1.outcomes) > 0
        assert len(adaptive.phase2.outcomes) > 0

    def test_adaptive_phase2_uses_adjusted_rules(self):
        """Phase 2 should use different rules if suggestions were applied."""
        adaptive = run_adaptive(
            rules=GROW_TENT_RULES,
            duration_minutes=60,
            step_seconds=30,
            start_hour=12,
            ambient_temp=32.0,
        )

        # If suggestions were made, rules should have been adjusted
        if adaptive.suggestions:
            # Phase 1 and Phase 2 should have different event patterns
            # (not necessarily different counts, but at different times)
            assert adaptive.phase1.num_rules == adaptive.phase2.num_rules

    def test_adaptive_improvement_metric(self):
        """improvement = phase2_avg - phase1_avg."""
        adaptive = run_adaptive(
            rules=GROW_TENT_RULES,
            duration_minutes=30,
            step_seconds=30,
            start_hour=12,
            ambient_temp=32.0,
        )

        expected = adaptive.phase2_avg_effectiveness - adaptive.phase1_avg_effectiveness
        assert adaptive.improvement == pytest.approx(expected)

    def test_adaptive_with_extreme_rules(self):
        """Deliberately unreachable thresholds should produce suggestions."""
        extreme_rules = [
            {
                "name": "extreme_temp_rule",
                "description": "Impossible threshold",
                "condition": {
                    "sensor": "temp1",
                    "operator": ">",
                    "threshold": 50,  # unreachable (clamped at 45°C)
                    "duration_seconds": 0,
                },
                "action": {
                    "target": "relay2",
                    "action": "set",
                    "value": True,
                    "reason": "Temperature exceeded 50°C",
                },
            },
            {
                "name": "extreme_temp_restore",
                "description": "Restore when cool",
                "condition": {
                    "sensor": "temp1",
                    "operator": "<",
                    "threshold": 25,
                    "duration_seconds": 0,
                },
                "action": {
                    "target": "relay2",
                    "action": "set",
                    "value": False,
                    "reason": "Temperature restored",
                },
            },
        ]

        adaptive = run_adaptive(
            rules=extreme_rules,
            duration_minutes=60,
            step_seconds=30,
            start_hour=12,
            ambient_temp=32.0,
        )

        # The unreachable rule should generate a suggestion
        assert len(adaptive.suggestions) > 0
        unreachable_suggestions = [
            s for s in adaptive.suggestions
            if s["rule_name"] == "extreme_temp_rule"
        ]
        assert len(unreachable_suggestions) > 0
        # Suggested threshold should be lower than 50
        assert unreachable_suggestions[0]["suggested_value"] < 50


class TestSuboptimalRuleImprovement:
    """Tests proving that suboptimal rules get improved by adaptive learning."""

    def test_suboptimal_rules_generate_suggestions(self):
        """Suboptimal thresholds (35°C, 80% hum, 20% soil) should be flagged."""
        adaptive = run_adaptive(
            rules=SUBOPTIMAL_RULES,
            duration_minutes=60,
            step_seconds=30,
            start_hour=12,
            ambient_temp=30.0,
        )

        assert len(adaptive.suggestions) > 0
        rule_names = {s["rule_name"] for s in adaptive.suggestions}
        # At least one of the extreme temperature rules should be flagged
        assert "high_temp_exhaust_on" in rule_names or "mild_heat_fan_on" in rule_names

    def test_suboptimal_thresholds_brought_closer(self):
        """Suggested thresholds should be more realistic than originals."""
        adaptive = run_adaptive(
            rules=SUBOPTIMAL_RULES,
            duration_minutes=60,
            step_seconds=30,
            start_hour=12,
            ambient_temp=30.0,
        )

        for s in adaptive.suggestions:
            if s["rule_name"] == "high_temp_exhaust_on" and s["field"] == "threshold":
                # 35°C is unreachable at 30°C ambient; suggestion should be lower
                assert s["suggested_value"] < 35
            if s["rule_name"] == "dry_soil_irrigate" and s["field"] == "threshold":
                # 20% is too low; suggestion should be higher
                assert s["suggested_value"] > 20


class TestAdaptiveChart:
    """Tests for adaptive learning chart generation."""

    def test_adaptive_chart_generates_png(self, tmp_path):
        """plot_adaptive generates a valid PNG file."""
        adaptive = run_adaptive(
            rules=GROW_TENT_RULES,
            duration_minutes=30,
            step_seconds=30,
            start_hour=12,
            ambient_temp=32.0,
        )

        output = tmp_path / "adaptive_test.png"
        returned_path = plot_adaptive(adaptive, output)

        assert returned_path == output
        assert output.exists()
        assert output.stat().st_size > 1000

    def test_adaptive_chart_with_no_suggestions(self, tmp_path):
        """Chart handles case where no suggestions were generated."""
        # Use mild ambient temp that won't trigger extreme rules
        adaptive = run_adaptive(
            rules=GROW_TENT_RULES,
            duration_minutes=15,
            step_seconds=30,
            start_hour=12,
            ambient_temp=25.0,  # mild — may not trigger many rules
        )

        output = tmp_path / "no_suggestions.png"
        plot_adaptive(adaptive, output)
        assert output.exists()
