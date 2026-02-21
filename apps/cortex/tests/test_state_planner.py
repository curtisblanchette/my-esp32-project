"""Unit tests for the MPC state planner."""

import copy
import time

import numpy as np
import pytest

from simulations.physics import PhysicsEngine, ACTUATOR_SPECS
from simulations.state_planner import (
    ACTUATOR_NAMES,
    DEFAULT_ACTUATOR_NAMES,
    NUM_ACTUATORS,
    WATTS,
    MAX_POWER,
    MPCConfig,
    GoalSpec,
    MPCResult,
    MPCPlanner,
    goals_from_dicts,
    hour_in_window,
)
from simulations.scenarios.default import GOALS, PROFILE, MPC_CONFIG


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def default_config():
    return MPCConfig()


@pytest.fixture
def default_goals():
    return goals_from_dicts(GOALS)


@pytest.fixture
def variable_actuators():
    """Temporarily set all actuators to variable control for MPC tests.

    MPC optimizes continuous intensities — binary quantization would prevent
    the solver from finding fractional optima.
    """
    originals = {rid: spec.control_type for rid, spec in ACTUATOR_SPECS.items()}
    for spec in ACTUATOR_SPECS.values():
        spec.control_type = "variable"
    yield
    for rid, ct in originals.items():
        ACTUATOR_SPECS[rid].control_type = ct


@pytest.fixture
def env():
    """Noiseless environment for deterministic tests."""
    return PhysicsEngine(noise=False)


@pytest.fixture
def planner(default_config, default_goals):
    return MPCPlanner(config=default_config, goals=default_goals)


# ── TestMPCConfig ───────────────────────────────────────────────────

class TestMPCConfig:

    def test_horizon_steps(self, default_config):
        # 15 min * 60 / 30s = 30 steps
        assert default_config.horizon_steps == 30

    def test_num_control_intervals(self, default_config):
        # 15 min * 60 / 150s = 6 intervals
        assert default_config.num_control_intervals == 6

    def test_steps_per_interval(self, default_config):
        # 150s / 30s = 5 steps
        assert default_config.steps_per_interval == 5

    def test_custom_horizon(self):
        cfg = MPCConfig(horizon_minutes=30.0, control_interval_s=300.0)
        assert cfg.num_control_intervals == 6
        assert cfg.steps_per_interval == 10

    def test_default_config_valid(self, default_config):
        assert default_config.w_goal > 0
        assert default_config.w_energy >= 0
        assert default_config.w_rate >= 0
        assert default_config.method in ("SLSQP", "L-BFGS-B")

    def test_from_mpc_config_dict(self):
        cfg = MPCConfig(**MPC_CONFIG)
        assert cfg.horizon_minutes == 15.0
        assert cfg.w_energy == 0.05


# ── TestGoalSpec ────────────────────────────────────────────────────

class TestGoalSpec:

    def test_goals_from_dicts(self):
        specs = goals_from_dicts(GOALS)
        assert len(specs) == len(GOALS)
        for spec in specs:
            assert spec.range_min < spec.range_max
            assert spec.target == (spec.range_min + spec.range_max) / 2.0
            assert spec.weight > 0

    def test_goals_from_dicts_parses_time_window(self):
        """Goals with timeWindow should have on_hour/off_hour set."""
        specs = goals_from_dicts(GOALS)
        # All GOALS now have timeWindows
        with_time = [s for s in specs if s.on_hour is not None]
        assert len(with_time) > 0
        # Check a day temp goal
        day_temp = [s for s in specs if s.metric == "temp1" and s.on_hour == 6]
        assert len(day_temp) == 1
        assert day_temp[0].off_hour == 22
        assert day_temp[0].range_min == 24.0
        assert day_temp[0].range_max == 28.0
        # Check a night temp goal
        night_temp = [s for s in specs if s.metric == "temp1" and s.on_hour == 22]
        assert len(night_temp) == 1
        assert night_temp[0].off_hour == 6
        assert night_temp[0].range_min == 18.0
        assert night_temp[0].range_max == 22.0

    def test_goals_from_dicts_no_time_window(self):
        """Goals without timeWindow should have on_hour/off_hour = None."""
        flat_goals = [
            {"metric": "temp1", "rangeMin": 20.0, "rangeMax": 28.0,
             "tolerance": 1.0, "priority": 1.0},
        ]
        specs = goals_from_dicts(flat_goals)
        assert specs[0].on_hour is None
        assert specs[0].off_hour is None

    def test_range_width(self):
        spec = GoalSpec(
            metric="temp1", target=24.0,
            range_min=20.0, range_max=28.0,
            tolerance=1.0, weight=1.0,
        )
        assert spec.range_width == 8.0

    def test_goal_metrics_match_scenario(self):
        specs = goals_from_dicts(GOALS)
        metrics = {s.metric for s in specs}
        assert "temp1" in metrics
        assert "hum1" in metrics


# ── TestMPCCostFunction ─────────────────────────────────────────────

class TestMPCCostFunction:

    def test_in_range_zero_cost(self, planner):
        """Readings inside all day goal ranges at noon should produce zero goal cost."""
        readings = {
            "temp1": 26.0,   # in day [24, 28]
            "hum1": 60.0,    # in day [55, 70]
            "soil1": 55.0,   # in day [45, 65]
            "co2_1": 1100.0, # in day [1000, 1300]
            "vpd1": 0.9,     # in day [0.8, 1.0]
        }
        trajectory = [(readings, 12.0)] * 3  # noon
        cost = planner._goal_cost(trajectory)
        assert cost == 0.0

    def test_out_of_range_positive_cost(self, planner, env):
        """Readings outside goal range should produce positive goal cost."""
        env.temperature = 40.0  # way above 28°C day max
        readings = env.get_readings()
        trajectory = [(readings, 12.0)] * 3  # noon
        cost = planner._goal_cost(trajectory)
        assert cost > 0.0

    def test_cost_increases_with_deviation(self, planner, env):
        """Larger deviations should produce larger costs."""
        env.temperature = 30.0
        r1 = env.get_readings()

        env.temperature = 40.0
        r2 = env.get_readings()

        c1 = planner._goal_cost([(r1, 12.0)])
        c2 = planner._goal_cost([(r2, 12.0)])
        assert c2 > c1

    def test_day_goals_active_at_noon(self, planner):
        """Day goals should contribute cost at noon, not night goals."""
        # Temperature at 20°C — in night range [18,22] but BELOW day range [24,28]
        readings = {"temp1": 20.0, "hum1": 60.0, "soil1": 55.0,
                    "co2_1": 1100.0, "vpd1": 0.9}
        noon_cost = planner._goal_cost([(readings, 12.0)])
        # Should have cost — 20°C is below day temp goal [24, 28]
        assert noon_cost > 0.0

    def test_night_goals_active_at_midnight(self, planner):
        """Night goals should contribute cost at midnight, not day goals."""
        # Temperature at 25°C — in day range [24,28] but ABOVE night range [18,22]
        readings = {"temp1": 25.0, "hum1": 55.0, "soil1": 50.0,
                    "co2_1": 500.0, "vpd1": 0.6}
        midnight_cost = planner._goal_cost([(readings, 0.0)])
        # Should have cost — 25°C is above night temp goal [18, 22]
        assert midnight_cost > 0.0

    def test_co2_goal_inactive_at_night(self, planner):
        """CO2 goal is day-only — should produce no CO2 cost at night."""
        # Low CO2 at night should be fine (no night CO2 goal)
        readings = {"temp1": 20.0, "hum1": 55.0, "soil1": 50.0,
                    "co2_1": 400.0, "vpd1": 0.6}  # all in night ranges
        night_cost = planner._goal_cost([(readings, 2.0)])
        # Now test the same at noon — should have CO2 cost
        day_cost = planner._goal_cost([(readings, 12.0)])
        # Day should have more cost because CO2 goal is active
        assert day_cost > night_cost

    def test_flat_goal_always_active(self):
        """Goals without timeWindow should be evaluated at all hours."""
        flat_goals = goals_from_dicts([
            {"metric": "temp1", "rangeMin": 20.0, "rangeMax": 28.0,
             "tolerance": 1.0, "priority": 1.0},
        ])
        planner = MPCPlanner(config=MPCConfig(), goals=flat_goals)
        readings = {"temp1": 35.0}
        noon_cost = planner._goal_cost([(readings, 12.0)])
        midnight_cost = planner._goal_cost([(readings, 0.0)])
        assert noon_cost > 0.0
        assert midnight_cost > 0.0
        assert noon_cost == midnight_cost

    def test_energy_proportional_to_power(self, planner):
        n = planner._cfg.num_control_intervals
        # All zeros = zero energy
        u_zero = np.zeros(n * NUM_ACTUATORS)
        assert planner._energy_cost(u_zero) == 0.0

        # All ones = max energy
        u_max = np.ones(n * NUM_ACTUATORS)
        e_max = planner._energy_cost(u_max)
        assert 0.9 < e_max <= 1.0  # normalised by MAX_POWER

    def test_rate_zero_for_constant(self, planner):
        """No change between intervals = zero rate cost."""
        n = planner._cfg.num_control_intervals
        current = np.full(NUM_ACTUATORS, 0.5)
        u = np.tile(current, n)
        assert planner._rate_cost(u, current) == pytest.approx(0.0, abs=1e-10)

    def test_rate_increases_with_change(self, planner):
        n = planner._cfg.num_control_intervals
        current = np.zeros(NUM_ACTUATORS)

        # Small change
        u_small = np.tile(np.full(NUM_ACTUATORS, 0.1), n)
        r_small = planner._rate_cost(u_small, current)

        # Large change
        u_large = np.tile(np.full(NUM_ACTUATORS, 0.9), n)
        r_large = planner._rate_cost(u_large, current)

        assert r_large > r_small


# ── TestMPCRollout ──────────────────────────────────────────────────

class TestMPCRollout:

    def test_rollout_preserves_original_with_save_restore(self, planner, env):
        """Rollout modifies env in-place; save/restore preserves original state."""
        temp_before = env.temperature
        snapshot = env.save_state()
        n = planner._cfg.num_control_intervals
        u = np.ones(n * NUM_ACTUATORS) * 0.5
        planner._rollout(u, env, 12.0)
        # Rollout mutates in place
        assert env.temperature != temp_before
        # Restore brings it back
        env.restore_state(snapshot)
        assert env.temperature == temp_before

    def test_rollout_advances_state(self, planner, env):
        """Rollout trajectory should show state evolution."""
        initial = env.get_readings()
        n = planner._cfg.num_control_intervals
        u = np.ones(n * NUM_ACTUATORS) * 0.5
        trajectory = planner._rollout(u, env, 12.0)
        assert len(trajectory) == n
        # Readings should differ from initial (captured before rollout)
        final_readings, final_hour = trajectory[-1]
        assert final_readings["temp1"] != initial["temp1"] or final_readings["hum1"] != initial["hum1"]

    def test_rollout_returns_hours(self, planner, env):
        """Rollout trajectory should include hour at each step."""
        n = planner._cfg.num_control_intervals
        u = np.zeros(n * NUM_ACTUATORS)
        trajectory = planner._rollout(u, env, 12.0)
        for readings, hour in trajectory:
            assert isinstance(readings, dict)
            assert isinstance(hour, float)
            assert 0.0 <= hour < 24.0

    def test_trajectory_length_matches_intervals(self, planner, env):
        n = planner._cfg.num_control_intervals
        u = np.zeros(n * NUM_ACTUATORS)
        trajectory = planner._rollout(u, env, 12.0)
        assert len(trajectory) == n

    def test_light_off_trajectory(self, planner, env):
        """With light=0, light_intensity should be near zero."""
        n = planner._cfg.num_control_intervals
        u = np.zeros(n * NUM_ACTUATORS)
        trajectory = planner._rollout(u, env, 12.0)
        for readings, _hour in trajectory:
            assert readings["light1"] < 10.0  # near zero (small noise possible from drift)

    def test_rollout_hour_wraps_at_midnight(self):
        """Hour should wrap around 24 when starting late."""
        env = PhysicsEngine(noise=False)
        cfg = MPCConfig(horizon_minutes=15.0, control_interval_s=150.0)
        goals = goals_from_dicts([
            {"metric": "temp1", "rangeMin": 20.0, "rangeMax": 28.0,
             "tolerance": 1.0, "priority": 1.0},
        ])
        planner = MPCPlanner(config=cfg, goals=goals)
        n = cfg.num_control_intervals
        u = np.zeros(n * NUM_ACTUATORS)
        trajectory = planner._rollout(u, env, 23.9)  # near midnight
        # All hours should still be valid
        for _readings, hour in trajectory:
            assert 0.0 <= hour < 24.0


# ── TestMPCSolver ───────────────────────────────────────────────────

class TestMPCSolver:

    def test_returns_valid_intensities(self, planner, env):
        result = planner.solve(env, 12.0)
        assert isinstance(result, MPCResult)
        for name, val in result.optimal_intensities.items():
            assert 0.0 <= val <= 1.0, f"{name}={val} out of bounds"

    def test_all_actuators_present(self, planner, env):
        result = planner.solve(env, 12.0)
        for name in ACTUATOR_NAMES:
            assert name in result.optimal_intensities

    def test_cost_is_finite(self, planner, env):
        result = planner.solve(env, 12.0)
        assert np.isfinite(result.cost)
        assert result.cost >= 0

    def test_solve_completes_under_1_second(self, planner, env):
        result = planner.solve(env, 12.0)
        assert result.solve_time_ms < 1000.0

    def test_warm_start_uses_previous_solution(self, planner, env):
        """Second solve should warm-start from previous solution with fewer iterations."""
        r1 = planner.solve(env, 12.0)
        assert planner._warm.previous_solution is not None
        # Step env slightly
        env.step(30.0, current_hour=12.0)
        r2 = planner.solve(env, 12.01)
        assert r1.success
        # Warm-started solve uses fewer iterations (warm_max_iterations=20)
        # and may hit the iteration limit, but should still produce a valid cost
        assert r2.cost < float("inf")
        assert r2.num_iterations <= planner._cfg.warm_max_iterations

    def test_high_temp_activates_cooling(self, env, default_goals, variable_actuators):
        """When temperature is high, MPC should increase exhaust/fan."""
        env.temperature = 38.0
        env.light = 0.8  # light on adds heat
        # Zero rate penalty so optimizer isn't discouraged from moving away from x0
        cfg = MPCConfig(horizon_minutes=10.0, control_interval_s=150.0, w_rate=0.0)
        planner = MPCPlanner(config=cfg, goals=default_goals)
        result = planner.solve(env, 12.0)
        # Exhaust fan or fan should be activated for cooling
        cooling = (
            result.optimal_intensities["exhaust_fan"]
            + result.optimal_intensities["fan"]
        )
        assert cooling > 0.2, f"Expected cooling > 0.2, got {cooling}"

    def test_low_co2_activates_injector(self, env, default_goals, variable_actuators):
        """When CO2 is low and lights are on, MPC should inject CO2."""
        env.co2 = 400.0
        env.light = 0.8
        cfg = MPCConfig(
            horizon_minutes=10.0,
            control_interval_s=150.0,
            w_rate=0.0,
            light_schedule={"on_hour": 6, "off_hour": 22},
        )
        planner = MPCPlanner(config=cfg, goals=default_goals)
        result = planner.solve(env, 12.0)
        co2_inj = result.optimal_intensities["co2_injector"]
        assert co2_inj > 0.05, f"Expected CO2 injector > 0.05, got {co2_inj}"

    def test_predicted_trajectory_is_readings_only(self, planner, env):
        """MPCResult.predicted_trajectory should contain plain readings dicts (no hour)."""
        result = planner.solve(env, 12.0)
        for entry in result.predicted_trajectory:
            assert isinstance(entry, dict)
            assert "temp1" in entry


# ── TestLightSchedule ──────────────────────────────────────────────

class TestLightSchedule:

    def test_light_off_during_night(self, default_goals):
        env = PhysicsEngine(noise=False)
        cfg = MPCConfig(
            horizon_minutes=5.0,
            control_interval_s=150.0,
            light_schedule={"on_hour": 6, "off_hour": 22},
        )
        planner = MPCPlanner(config=cfg, goals=default_goals)
        result = planner.solve(env, 23.0)  # 11 PM
        assert result.optimal_intensities["light"] == 0.0
        assert result.optimal_intensities["co2_injector"] == 0.0

    def test_light_allowed_during_day(self, default_goals):
        env = PhysicsEngine(noise=False)
        env.temperature = 24.0  # comfortable temp
        cfg = MPCConfig(
            horizon_minutes=5.0,
            control_interval_s=150.0,
            light_schedule={"on_hour": 6, "off_hour": 22},
        )
        planner = MPCPlanner(config=cfg, goals=default_goals)
        result = planner.solve(env, 12.0)  # noon
        # Light should be non-zero (optimizer can choose any value 0-1)
        # Just verify it's not forced to zero
        bounds = planner._build_bounds(12.0)
        light_idx = ACTUATOR_NAMES.index("light")
        _, hi = bounds[light_idx]
        assert hi == 1.0

    def test_hour_in_window_normal(self):
        assert hour_in_window(12.0, 6.0, 22.0) is True
        assert hour_in_window(5.0, 6.0, 22.0) is False
        assert hour_in_window(22.5, 6.0, 22.0) is False

    def test_hour_in_window_wrap(self):
        # Night schedule: on=22, off=6 means 22-24 and 0-6
        assert hour_in_window(23.0, 22.0, 6.0) is True
        assert hour_in_window(3.0, 22.0, 6.0) is True
        assert hour_in_window(12.0, 22.0, 6.0) is False


# ── TestConstants ───────────────────────────────────────────────────

class TestAutoScale:
    """Tests for MPCConfig.auto_scale volume-based scaling."""

    def test_no_change_for_tent(self):
        """Tent volume (2.88 m³) should return identical config."""
        base = MPCConfig()
        scaled = MPCConfig.auto_scale(base, 2.88)
        assert scaled.horizon_minutes == base.horizon_minutes
        assert scaled.w_energy == base.w_energy
        assert scaled.w_rate == base.w_rate

    def test_no_change_for_small_volume(self):
        """Volumes at or below reference should not be scaled."""
        base = MPCConfig()
        scaled = MPCConfig.auto_scale(base, 1.0)
        assert scaled.horizon_minutes == base.horizon_minutes
        assert scaled.w_energy == base.w_energy

    def test_increases_horizon_for_large_room(self):
        """300 m³ room should get a longer horizon (up to 4× cap)."""
        base = MPCConfig(horizon_minutes=15.0)
        scaled = MPCConfig.auto_scale(base, 300.0)
        assert scaled.horizon_minutes == 15.0 * 4.0  # capped at 4×

    def test_reduces_energy_weight(self):
        """300 m³ room should get lower energy weight."""
        base = MPCConfig(w_energy=0.05)
        scaled = MPCConfig.auto_scale(base, 300.0)
        ratio = 300.0 / 2.88
        expected = 0.05 / (ratio ** 0.5)
        assert scaled.w_energy == pytest.approx(expected, rel=1e-6)

    def test_reduces_rate_weight(self):
        """300 m³ room should get lower rate weight."""
        base = MPCConfig(w_rate=0.1)
        scaled = MPCConfig.auto_scale(base, 300.0)
        ratio = 300.0 / 2.88
        expected = 0.1 / (ratio ** 0.5)
        assert scaled.w_rate == pytest.approx(expected, rel=1e-6)

    def test_preserves_goal_weight(self):
        """Goal weight should always remain unchanged."""
        base = MPCConfig(w_goal=2.0)
        scaled = MPCConfig.auto_scale(base, 300.0)
        assert scaled.w_goal == 2.0

    def test_scale_capped_at_4x(self):
        """Even for enormous volumes, scale factor caps at 4×."""
        base = MPCConfig(horizon_minutes=15.0)
        scaled = MPCConfig.auto_scale(base, 10000.0)
        assert scaled.horizon_minutes == 15.0 * 4.0

    def test_preserves_method_and_schedule(self):
        base = MPCConfig(method="SLSQP", light_schedule={"on_hour": 6, "off_hour": 22})
        scaled = MPCConfig.auto_scale(base, 300.0)
        assert scaled.method == "SLSQP"
        assert scaled.light_schedule == {"on_hour": 6, "off_hour": 22}


class TestConstants:

    def test_actuator_names_count(self):
        assert NUM_ACTUATORS == 7

    def test_watts_vector_length(self):
        assert len(WATTS) == NUM_ACTUATORS

    def test_max_power_positive(self):
        assert MAX_POWER > 0

    def test_actuator_names_match_env(self):
        env = PhysicsEngine()
        for name in ACTUATOR_NAMES:
            assert hasattr(env, name), f"Env missing actuator: {name}"


# ── TestBinaryRelaxation ───────────────────────────────────────────

class TestBinaryRelaxation:
    """Verify MPC rollout relaxes binary quantization for smooth gradients."""

    def test_rollout_uses_relaxed_quantization(self, planner, env):
        """During rollout, the cloned env should allow fractional binary intensities."""
        n = planner._cfg.num_control_intervals
        # Set a binary actuator (fan) to a fractional value
        u = np.zeros(n * NUM_ACTUATORS)
        fan_idx = ACTUATOR_NAMES.index("fan")
        for k in range(n):
            u[k * NUM_ACTUATORS + fan_idx] = 0.3  # fractional
        trajectory = planner._rollout(u, env, 12.0)
        # If relaxation works, we should get a trajectory (rollout didn't snap to 0)
        # and the original env should be unchanged
        assert len(trajectory) == n
        assert env.fan == 0.0  # original untouched

    def test_final_output_quantizes_binary(self, default_goals):
        """solve() output for binary actuators should be exactly 0.0 or 1.0."""
        env = PhysicsEngine(noise=False)
        planner = MPCPlanner(config=MPCConfig(), goals=default_goals)
        result = planner.solve(env, 12.0)
        for name in ACTUATOR_NAMES:
            spec = ACTUATOR_SPECS.get(
                next((r for r, s in ACTUATOR_SPECS.items() if s.name == name), ""),
            )
            if spec and spec.control_type == "binary":
                val = result.optimal_intensities[name]
                assert val in (0.0, 1.0), (
                    f"Binary actuator {name} has fractional output {val}"
                )

    def test_high_temp_activates_cooling_without_variable_fixture(self, default_goals):
        """MPC should find cooling actions with binary actuators (no variable_actuators fixture)."""
        env = PhysicsEngine(noise=False)
        env.temperature = 38.0
        env.light = 0.8
        cfg = MPCConfig(horizon_minutes=10.0, control_interval_s=150.0, w_rate=0.0)
        planner = MPCPlanner(config=cfg, goals=default_goals)
        result = planner.solve(env, 12.0)
        cooling = (
            result.optimal_intensities["exhaust_fan"]
            + result.optimal_intensities["fan"]
        )
        assert cooling > 0.0, f"Expected cooling > 0, got {cooling}"

    def test_low_co2_activates_injector_without_variable_fixture(self, default_goals):
        """MPC should activate CO2 injector (binary) without needing variable_actuators."""
        env = PhysicsEngine(noise=False)
        env.co2 = 400.0
        env.light = 0.8
        cfg = MPCConfig(
            horizon_minutes=10.0, control_interval_s=150.0,
            w_rate=0.0, w_energy=0.0,
            light_schedule={"on_hour": 6, "off_hour": 22},
        )
        planner = MPCPlanner(config=cfg, goals=default_goals)
        result = planner.solve(env, 12.0)
        assert result.optimal_intensities["co2_injector"] == 1.0, (
            f"Expected CO2 injector ON, got {result.optimal_intensities['co2_injector']}"
        )


# ── TestDynamicActuators ──────────────────────────────────────────

class TestDynamicActuators:
    """Tests for dynamic actuator discovery (HVAC support)."""

    @pytest.fixture
    def hvac_specs(self):
        """Actuator specs including HVAC."""
        from simulations.physics import ActuatorSpec
        return {
            "relay1": ActuatorSpec(name="fan", relay_id="relay1", max_watts=45),
            "relay2": ActuatorSpec(name="exhaust_fan", relay_id="relay2", max_watts=85),
            "relay3": ActuatorSpec(name="humidifier", relay_id="relay3", max_watts=30,
                                   humidify_g_per_min=5.0),
            "relay4": ActuatorSpec(name="dehumidifier", relay_id="relay4", max_watts=300,
                                   dehumidify_g_per_min=12.5),
            "relay5": ActuatorSpec(name="irrigation", relay_id="relay5", max_watts=15),
            "relay6": ActuatorSpec(name="light", relay_id="relay6", max_watts=480),
            "relay7": ActuatorSpec(name="co2_injector", relay_id="relay7", max_watts=10),
            "relay8": ActuatorSpec(name="hvac", relay_id="relay8", max_watts=3500,
                                   control_type="variable"),
        }

    def test_planner_includes_hvac(self, default_goals, hvac_specs):
        """Planner with HVAC specs should have 8 actuators."""
        planner = MPCPlanner(config=MPCConfig(), goals=default_goals, actuator_specs=hvac_specs)
        assert "hvac" in planner.actuator_names
        assert len(planner.actuator_names) == 8

    def test_planner_without_hvac_has_7(self, default_goals):
        """Default planner (no HVAC) should have 7 actuators."""
        planner = MPCPlanner(config=MPCConfig(), goals=default_goals)
        assert "hvac" not in planner.actuator_names
        assert len(planner.actuator_names) == 7

    def test_hvac_canonical_order_preserved(self, default_goals, hvac_specs):
        """HVAC should appear after the 7 canonical actuators."""
        planner = MPCPlanner(config=MPCConfig(), goals=default_goals, actuator_specs=hvac_specs)
        names = planner.actuator_names
        # First 7 should be the canonical order
        for i, expected in enumerate(DEFAULT_ACTUATOR_NAMES):
            assert names[i] == expected
        # HVAC should be appended
        assert names[7] == "hvac"

    def test_solve_with_hvac(self, default_goals, hvac_specs):
        """Planner should solve successfully with HVAC actuator."""
        env = PhysicsEngine(noise=False, actuator_specs=hvac_specs)
        planner = MPCPlanner(config=MPCConfig(), goals=default_goals, actuator_specs=hvac_specs)
        result = planner.solve(env, 12.0)
        assert result.success
        assert "hvac" in result.optimal_intensities
        assert 0.0 <= result.optimal_intensities["hvac"] <= 1.0

    def test_hvac_activates_when_hot(self, default_goals, hvac_specs):
        """With HVAC available, MPC should use cooling actuators when temperature is high."""
        env = PhysicsEngine(noise=False, actuator_specs=hvac_specs)
        env.temperature = 38.0
        env.light = 0.8
        cfg = MPCConfig(horizon_minutes=10.0, control_interval_s=150.0, w_rate=0.0, w_energy=0.0)
        planner = MPCPlanner(config=cfg, goals=default_goals, actuator_specs=hvac_specs)
        result = planner.solve(env, 12.0)
        # At least some cooling actuator should be non-zero
        # (HVAC is so powerful in 2.88m³ that even tiny intensity suffices)
        cooling = (
            result.optimal_intensities["fan"]
            + result.optimal_intensities["exhaust_fan"]
            + result.optimal_intensities["hvac"]
        )
        assert cooling > 0.0, f"Expected any cooling, got {cooling}"

    def test_actuator_names_property_is_copy(self, default_goals):
        """actuator_names property should return a copy, not the internal list."""
        planner = MPCPlanner(config=MPCConfig(), goals=default_goals)
        names = planner.actuator_names
        names.append("bogus")
        assert "bogus" not in planner.actuator_names
