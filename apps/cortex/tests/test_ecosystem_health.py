"""Tests for ecosystem health scoring engine."""

from datetime import datetime

import pytest

from src.services.ecosystem_health import (
    effective_range,
    score_goal,
    score_relay_schedule,
    compute_energy,
    compute_health,
    EcosystemHealth,
    EnergySnapshot,
    STRATEGY_TOLERANCE_MULTIPLIER,
)


class TestEffectiveRange:
    """Strategy-adjusted tolerance ranges."""

    def test_precision_no_tolerance(self):
        """Precision strategy: tolerance is zero."""
        lo, hi = effective_range(24.0, 28.0, tolerance=1.0, strategy="precision")
        assert lo == 24.0
        assert hi == 28.0

    def test_balanced_uses_tolerance(self):
        """Balanced strategy: uses configured tolerance (1x)."""
        lo, hi = effective_range(24.0, 28.0, tolerance=1.0, strategy="balanced")
        assert lo == 23.0
        assert hi == 29.0

    def test_efficiency_doubles_tolerance(self):
        """Efficiency strategy: doubles tolerance."""
        lo, hi = effective_range(24.0, 28.0, tolerance=1.0, strategy="efficiency")
        assert lo == 22.0
        assert hi == 30.0

    def test_zero_tolerance(self):
        """Zero tolerance — all strategies produce same range."""
        for strategy in ["precision", "balanced", "efficiency"]:
            lo, hi = effective_range(24.0, 28.0, tolerance=0.0, strategy=strategy)
            assert lo == 24.0
            assert hi == 28.0

    def test_open_ended_min(self):
        """No lower bound."""
        lo, hi = effective_range(None, 28.0, tolerance=1.0, strategy="balanced")
        assert lo is None
        assert hi == 29.0

    def test_open_ended_max(self):
        """No upper bound."""
        lo, hi = effective_range(24.0, None, tolerance=1.0, strategy="balanced")
        assert lo == 23.0
        assert hi is None


class TestScoreGoal:
    """Individual goal scoring."""

    def test_in_range(self):
        assert score_goal(26.0, 24.0, 28.0, 0.0, "balanced") == 1.0

    def test_at_min_boundary(self):
        assert score_goal(24.0, 24.0, 28.0, 0.0, "balanced") == 1.0

    def test_at_max_boundary(self):
        assert score_goal(28.0, 24.0, 28.0, 0.0, "balanced") == 1.0

    def test_slightly_below(self):
        score = score_goal(23.0, 24.0, 28.0, 0.0, "balanced")
        assert 0 < score < 1.0  # degraded but not zero

    def test_slightly_above(self):
        score = score_goal(29.0, 24.0, 28.0, 0.0, "balanced")
        assert 0 < score < 1.0

    def test_far_out_of_range(self):
        score = score_goal(40.0, 24.0, 28.0, 0.0, "balanced")
        assert score == 0.0

    def test_tolerance_brings_into_range(self):
        """Value outside raw range but within tolerance."""
        # 23.5 is below 24.0 but within tolerance=1.0 for balanced
        score = score_goal(23.5, 24.0, 28.0, 1.0, "balanced")
        assert score == 1.0

    def test_precision_strict(self):
        """Precision doesn't use tolerance."""
        score = score_goal(23.5, 24.0, 28.0, 1.0, "precision")
        assert score < 1.0

    def test_efficiency_lenient(self):
        """Efficiency doubles tolerance — wider acceptance."""
        score = score_goal(22.5, 24.0, 28.0, 1.0, "efficiency")
        assert score == 1.0  # 22.0 is effective min with 2x tolerance

    def test_open_ended_min_only(self):
        """Only lower bound — above is always in range."""
        assert score_goal(100.0, 40.0, None, 0.0, "balanced") == 1.0
        score = score_goal(30.0, 40.0, None, 0.0, "balanced")
        assert score < 1.0

    def test_open_ended_max_only(self):
        """Only upper bound — below is always in range."""
        assert score_goal(0.0, None, 28.0, 0.0, "balanced") == 1.0
        score = score_goal(35.0, None, 28.0, 0.0, "balanced")
        assert score < 1.0


class TestScoreRelaySchedule:
    """Relay schedule compliance scoring."""

    @pytest.fixture
    def light_schedule(self):
        return {
            "06:00-22:00": {"expected": True},
            "22:00-06:00": {"expected": False},
        }

    def test_light_on_during_day(self, light_schedule):
        noon = datetime(2026, 2, 16, 12, 0)
        assert score_relay_schedule(True, light_schedule, now=noon) == 1.0

    def test_light_off_during_day(self, light_schedule):
        noon = datetime(2026, 2, 16, 12, 0)
        assert score_relay_schedule(False, light_schedule, now=noon) == 0.0

    def test_light_off_at_night(self, light_schedule):
        midnight = datetime(2026, 2, 16, 23, 30)
        assert score_relay_schedule(False, light_schedule, now=midnight) == 1.0

    def test_light_on_at_night(self, light_schedule):
        midnight = datetime(2026, 2, 16, 23, 30)
        assert score_relay_schedule(True, light_schedule, now=midnight) == 0.0

    def test_early_morning_night(self, light_schedule):
        """03:00 is in the overnight range 22:00-06:00."""
        early = datetime(2026, 2, 16, 3, 0)
        assert score_relay_schedule(False, light_schedule, now=early) == 1.0

    def test_no_matching_window(self):
        """No schedule match — default compliant."""
        score = score_relay_schedule(True, {}, now=datetime(2026, 2, 16, 12, 0))
        assert score == 1.0


class TestComputeEnergy:
    """Energy computation from runtime and watts."""

    def test_basic_energy(self):
        energy = compute_energy(
            actuator_runtime={"relay2": 10.0, "relay3": 5.0},
            actuator_watts={"relay2": 45.0, "relay3": 30.0},
        )
        assert energy.total_wh == pytest.approx(10.0, abs=0.01)
        # relay2: 45W * 10min/60 = 7.5 Wh
        assert energy.per_actuator_wh["relay2"] == pytest.approx(7.5, abs=0.01)
        # relay3: 30W * 5min/60 = 2.5 Wh
        assert energy.per_actuator_wh["relay3"] == pytest.approx(2.5, abs=0.01)

    def test_fallback_watts(self):
        """Unknown relay uses 25W fallback."""
        energy = compute_energy(
            actuator_runtime={"relay9": 60.0},
            actuator_watts={},
        )
        # 25W * 60min/60 = 25 Wh
        assert energy.total_wh == pytest.approx(25.0, abs=0.01)

    def test_empty_runtime(self):
        energy = compute_energy({}, {})
        assert energy.total_wh == 0.0

    def test_runtime_preserved(self):
        energy = compute_energy(
            actuator_runtime={"relay2": 15.5},
            actuator_watts={"relay2": 45.0},
        )
        assert energy.per_actuator_runtime_min["relay2"] == 15.5


class TestComputeHealth:
    """Full ecosystem health scoring."""

    @pytest.fixture
    def flower_profile(self):
        return {
            "strategy": "balanced",
            "phase": "flower",
        }

    @pytest.fixture
    def basic_goals(self):
        return [
            {"metric": "temp1", "metricType": "sensor", "rangeMin": 24.0, "rangeMax": 28.0, "tolerance": 0.0, "priority": 1.0, "phase": None},
            {"metric": "hum1", "metricType": "sensor", "rangeMin": 45.0, "rangeMax": 65.0, "tolerance": 0.0, "priority": 1.0, "phase": None},
        ]

    def test_all_in_range(self, flower_profile, basic_goals):
        health = compute_health(
            profile=flower_profile,
            goals=basic_goals,
            readings={"temp1": 26.0, "hum1": 55.0},
        )
        assert health.score == 1.0
        assert health.goal_scores["temp1"] == 1.0
        assert health.goal_scores["hum1"] == 1.0
        assert health.out_of_range == []

    def test_one_out_of_range(self, flower_profile, basic_goals):
        health = compute_health(
            profile=flower_profile,
            goals=basic_goals,
            readings={"temp1": 26.0, "hum1": 80.0},  # humidity too high
        )
        assert health.score < 1.0
        assert health.goal_scores["temp1"] == 1.0
        assert health.goal_scores["hum1"] < 1.0
        assert "hum1" in health.out_of_range

    def test_both_out_of_range(self, flower_profile, basic_goals):
        health = compute_health(
            profile=flower_profile,
            goals=basic_goals,
            readings={"temp1": 35.0, "hum1": 80.0},
        )
        assert health.score < 0.5
        assert len(health.out_of_range) == 2

    def test_priority_weighting(self, flower_profile):
        """Higher priority goals have more influence on score."""
        goals = [
            {"metric": "temp1", "metricType": "sensor", "rangeMin": 24.0, "rangeMax": 28.0, "tolerance": 0.0, "priority": 1.0, "phase": None},
            {"metric": "vpd", "metricType": "derived", "rangeMin": 1.0, "rangeMax": 1.3, "tolerance": 0.0, "priority": 3.0, "phase": None},
        ]
        # VPD in range (25°C, 55% → ~1.43 kPa — slightly out)
        # temp in range
        health = compute_health(
            profile=flower_profile,
            goals=goals,
            readings={"temp1": 26.0, "hum1": 55.0},  # VPD ~1.5
        )
        # VPD is out of range and weighted 3x, so it dominates the score
        assert health.goal_scores["temp1"] == 1.0
        assert health.goal_scores["vpd"] < 1.0

    def test_derived_vpd_goal(self, flower_profile):
        """VPD derived metric scored against goal."""
        goals = [
            {"metric": "vpd", "metricType": "derived", "rangeMin": 0.8, "rangeMax": 1.4, "tolerance": 0.0, "priority": 1.0, "phase": None},
        ]
        # 25°C, 60% RH → VPD ≈ 1.27 kPa (in range)
        health = compute_health(
            profile=flower_profile,
            goals=goals,
            readings={"temp1": 25.0, "hum1": 60.0},
        )
        assert health.score == 1.0
        assert "vpd" in health.goal_scores

    def test_relay_schedule_goal(self, flower_profile):
        """Relay schedule compliance."""
        goals = [
            {
                "metric": "relay6", "metricType": "relay_schedule",
                "schedule": {"06:00-22:00": {"expected": True}, "22:00-06:00": {"expected": False}},
                "priority": 1.0, "phase": None, "rangeMin": None, "rangeMax": None, "tolerance": 0.0,
            },
        ]
        noon = datetime(2026, 2, 16, 12, 0)
        health = compute_health(
            profile=flower_profile,
            goals=goals,
            readings={},
            relay_states={"relay6": True},
            now=noon,
        )
        assert health.score == 1.0

    def test_phase_filtering(self, flower_profile):
        """Goals for a different phase are skipped."""
        goals = [
            {"metric": "temp1", "metricType": "sensor", "rangeMin": 24.0, "rangeMax": 28.0, "tolerance": 0.0, "priority": 1.0, "phase": "flower"},
            {"metric": "hum1", "metricType": "sensor", "rangeMin": 90.0, "rangeMax": 95.0, "tolerance": 0.0, "priority": 1.0, "phase": "seedling"},
        ]
        # Seedling humidity goal (90-95%) would fail, but it's for wrong phase
        health = compute_health(
            profile=flower_profile,
            goals=goals,
            readings={"temp1": 26.0, "hum1": 55.0},
        )
        assert health.score == 1.0
        assert "hum1" not in health.goal_scores  # skipped

    def test_energy_penalty_efficiency(self):
        """Efficiency strategy applies energy penalty."""
        profile = {"strategy": "efficiency", "phase": "flower"}
        goals = [
            {"metric": "temp1", "metricType": "sensor", "rangeMin": 24.0, "rangeMax": 28.0, "tolerance": 0.0, "priority": 1.0, "phase": None},
        ]
        health = compute_health(
            profile=profile,
            goals=goals,
            readings={"temp1": 26.0},
            actuator_runtime={"relay2": 60.0},
            actuator_watts={"relay2": 100.0},
        )
        # All in range = 1.0, but energy penalty from 100Wh
        assert health.score < 1.0
        assert health.energy is not None
        assert health.energy.total_wh == pytest.approx(100.0, abs=0.1)

    def test_no_energy_penalty_precision(self):
        """Precision strategy: no energy penalty."""
        profile = {"strategy": "precision", "phase": "flower"}
        goals = [
            {"metric": "temp1", "metricType": "sensor", "rangeMin": 24.0, "rangeMax": 28.0, "tolerance": 0.0, "priority": 1.0, "phase": None},
        ]
        health = compute_health(
            profile=profile,
            goals=goals,
            readings={"temp1": 26.0},
            actuator_runtime={"relay2": 60.0},
            actuator_watts={"relay2": 100.0},
        )
        assert health.score == 1.0  # no penalty

    def test_no_goals(self, flower_profile):
        """No goals — score is 0 (nothing to be compliant with)."""
        health = compute_health(
            profile=flower_profile, goals=[], readings={"temp1": 26.0}
        )
        assert health.score == 0.0

    def test_missing_sensor_for_goal(self, flower_profile, basic_goals):
        """Goal references a sensor not in readings — skipped."""
        health = compute_health(
            profile=flower_profile,
            goals=basic_goals,
            readings={"temp1": 26.0},  # no hum1
        )
        # Only temp1 scored
        assert "temp1" in health.goal_scores
        assert "hum1" not in health.goal_scores


class TestTimeWindowFiltering:
    """Day/night time window goal filtering."""

    @pytest.fixture
    def profile(self):
        return {"strategy": "balanced", "phase": "flower"}

    def test_day_goal_scored_during_day(self, profile):
        """Goal with day time window is scored when now is during the day."""
        goals = [
            {
                "metric": "temp1", "metricType": "sensor",
                "rangeMin": 24.0, "rangeMax": 28.0, "tolerance": 0.0,
                "priority": 1.0, "phase": None,
                "timeWindow": {"onHour": 6, "offHour": 22},
            },
        ]
        health = compute_health(
            profile=profile, goals=goals,
            readings={"temp1": 26.0},
            now=datetime(2026, 2, 19, 14, 0),
        )
        assert "temp1" in health.goal_scores
        assert health.score == 1.0

    def test_day_goal_skipped_at_night(self, profile):
        """Goal with day time window is skipped when now is night."""
        goals = [
            {
                "metric": "temp1", "metricType": "sensor",
                "rangeMin": 24.0, "rangeMax": 28.0, "tolerance": 0.0,
                "priority": 1.0, "phase": None,
                "timeWindow": {"onHour": 6, "offHour": 22},
            },
        ]
        health = compute_health(
            profile=profile, goals=goals,
            readings={"temp1": 26.0},
            now=datetime(2026, 2, 19, 2, 0),
        )
        assert "temp1" not in health.goal_scores

    def test_night_goal_scored_at_night(self, profile):
        """Goal with night time window (wrap-around) is scored at 2am."""
        goals = [
            {
                "metric": "temp1", "metricType": "sensor",
                "rangeMin": 18.0, "rangeMax": 22.0, "tolerance": 0.0,
                "priority": 1.0, "phase": None,
                "timeWindow": {"onHour": 22, "offHour": 6},
            },
        ]
        health = compute_health(
            profile=profile, goals=goals,
            readings={"temp1": 20.0},
            now=datetime(2026, 2, 19, 2, 0),
        )
        assert "temp1" in health.goal_scores
        assert health.score == 1.0

    def test_always_active_goal(self, profile):
        """Goal without timeWindow is scored at all hours."""
        goals = [
            {
                "metric": "temp1", "metricType": "sensor",
                "rangeMin": 24.0, "rangeMax": 28.0, "tolerance": 0.0,
                "priority": 1.0, "phase": None,
            },
        ]
        for hour in [2, 8, 14, 23]:
            health = compute_health(
                profile=profile, goals=goals,
                readings={"temp1": 26.0},
                now=datetime(2026, 2, 19, hour, 0),
            )
            assert "temp1" in health.goal_scores

    def test_overall_health_reflects_active_goals_only(self, profile):
        """Mixed day/night goals — score uses only currently active ones."""
        goals = [
            {
                "metric": "temp1", "metricType": "sensor",
                "rangeMin": 24.0, "rangeMax": 28.0, "tolerance": 0.0,
                "priority": 1.0, "phase": None,
                "timeWindow": {"onHour": 6, "offHour": 22},
            },
            {
                "metric": "hum1", "metricType": "sensor",
                "rangeMin": 40.0, "rangeMax": 50.0, "tolerance": 0.0,
                "priority": 1.0, "phase": None,
                "timeWindow": {"onHour": 22, "offHour": 6},
            },
        ]
        # At 14:00 (day): only temp1 is active, hum1 is night-only
        health = compute_health(
            profile=profile, goals=goals,
            readings={"temp1": 26.0, "hum1": 80.0},  # hum1 way out of range
            now=datetime(2026, 2, 19, 14, 0),
        )
        assert "temp1" in health.goal_scores
        assert "hum1" not in health.goal_scores
        assert health.score == 1.0  # only temp1 scored, and it's in range
