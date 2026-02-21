"""Tests for the duct physics module — fan curves, system resistance, ACH."""

import math

import pytest

from simulations.duct_physics import (
    FRICTION_FLEX,
    FRICTION_SMOOTH,
    build_ventilation_fn,
    compute_ach,
    compute_operating_flow_m3s,
    compute_system_resistance,
    _CFM_TO_M3S,
    _INWC_TO_PA,
    _INCH_TO_M,
    _FT_TO_M,
    _CARBON_FILTER_PA,
    _CARBON_FILTER_Q_REF,
)


# ---------------------------------------------------------------------------
# System resistance
# ---------------------------------------------------------------------------

class TestSystemResistance:
    """Tests for compute_system_resistance()."""

    def test_longer_duct_higher_resistance(self):
        short = compute_system_resistance(0.15, 3.0, FRICTION_SMOOTH)
        long = compute_system_resistance(0.15, 10.0, FRICTION_SMOOTH)
        assert long > short

    def test_smaller_diameter_much_higher_resistance(self):
        big = compute_system_resistance(0.25, 5.0, FRICTION_SMOOTH)
        small = compute_system_resistance(0.15, 5.0, FRICTION_SMOOTH)
        # Resistance scales as 1/D^5 roughly — should be dramatically higher
        assert small > big * 3

    def test_flex_duct_higher_than_smooth(self):
        smooth = compute_system_resistance(0.15, 5.0, FRICTION_SMOOTH)
        flex = compute_system_resistance(0.15, 5.0, FRICTION_FLEX)
        assert flex == pytest.approx(smooth * 2, rel=0.01)

    def test_elbows_increase_resistance(self):
        no_elbows = compute_system_resistance(0.15, 5.0, FRICTION_SMOOTH, num_elbows=0)
        two_elbows = compute_system_resistance(0.15, 5.0, FRICTION_SMOOTH, num_elbows=2)
        assert two_elbows > no_elbows

    def test_carbon_filter_increases_resistance(self):
        no_filter = compute_system_resistance(0.15, 5.0, FRICTION_SMOOTH, has_carbon_filter=False)
        with_filter = compute_system_resistance(0.15, 5.0, FRICTION_SMOOTH, has_carbon_filter=True)
        assert with_filter > no_filter

    def test_carbon_filter_adds_expected_k(self):
        no_filter = compute_system_resistance(0.15, 5.0, FRICTION_SMOOTH, has_carbon_filter=False)
        with_filter = compute_system_resistance(0.15, 5.0, FRICTION_SMOOTH, has_carbon_filter=True)
        expected_k_filter = _CARBON_FILTER_PA / (_CARBON_FILTER_Q_REF ** 2)
        assert with_filter - no_filter == pytest.approx(expected_k_filter, rel=1e-6)

    def test_positive_result(self):
        k = compute_system_resistance(0.15, 5.0, FRICTION_SMOOTH)
        assert k > 0


# ---------------------------------------------------------------------------
# Fan operating point
# ---------------------------------------------------------------------------

class TestOperatingFlow:
    """Tests for compute_operating_flow_m3s()."""

    def test_zero_speed_zero_flow(self):
        flow = compute_operating_flow_m3s(0.2, 300.0, 1000.0, speed_fraction=0.0)
        assert flow == 0.0

    def test_zero_resistance_gives_free_air(self):
        q_max = 0.2  # m³/s
        flow = compute_operating_flow_m3s(q_max, 300.0, system_k=0.0, speed_fraction=1.0)
        assert flow == pytest.approx(q_max, rel=1e-6)

    def test_half_speed_roughly_half_flow(self):
        """Fan affinity: flow ~ speed (with some system resistance reduction)."""
        full = compute_operating_flow_m3s(0.2, 300.0, 500.0, speed_fraction=1.0)
        half = compute_operating_flow_m3s(0.2, 300.0, 500.0, speed_fraction=0.5)
        # With system resistance, half speed gives somewhat less than half flow
        assert 0.3 * full < half < 0.6 * full

    def test_flow_less_than_free_air_with_resistance(self):
        q_max = 0.2
        flow = compute_operating_flow_m3s(q_max, 300.0, 1000.0, speed_fraction=1.0)
        assert 0 < flow < q_max

    def test_higher_resistance_lower_flow(self):
        low_k = compute_operating_flow_m3s(0.2, 300.0, 500.0, speed_fraction=1.0)
        high_k = compute_operating_flow_m3s(0.2, 300.0, 5000.0, speed_fraction=1.0)
        assert high_k < low_k

    def test_negative_speed_zero_flow(self):
        flow = compute_operating_flow_m3s(0.2, 300.0, 1000.0, speed_fraction=-0.5)
        assert flow == 0.0

    def test_speed_clamped_at_one(self):
        at_one = compute_operating_flow_m3s(0.2, 300.0, 1000.0, speed_fraction=1.0)
        at_two = compute_operating_flow_m3s(0.2, 300.0, 1000.0, speed_fraction=2.0)
        assert at_two == pytest.approx(at_one, rel=1e-6)

    def test_monotonically_increasing_with_speed(self):
        speeds = [0.0, 0.25, 0.5, 0.75, 1.0]
        flows = [
            compute_operating_flow_m3s(0.2, 300.0, 1000.0, speed_fraction=s)
            for s in speeds
        ]
        for i in range(1, len(flows)):
            assert flows[i] >= flows[i - 1]


# ---------------------------------------------------------------------------
# ACH conversion
# ---------------------------------------------------------------------------

class TestComputeACH:
    """Tests for compute_ach()."""

    def test_zero_flow_returns_passive(self):
        ach = compute_ach(0.0, 10.0, passive_ach=0.5)
        assert ach == pytest.approx(0.5)

    def test_known_values(self):
        # 0.1 m³/s in a 100 m³ room = 3.6 ACH (plus passive)
        ach = compute_ach(0.1, 100.0, passive_ach=0.0)
        assert ach == pytest.approx(3.6, rel=1e-6)

    def test_smaller_volume_higher_ach(self):
        big = compute_ach(0.1, 200.0)
        small = compute_ach(0.1, 50.0)
        assert small > big

    def test_zero_volume_returns_passive(self):
        ach = compute_ach(0.1, 0.0, passive_ach=1.0)
        assert ach == 1.0


# ---------------------------------------------------------------------------
# build_ventilation_fn
# ---------------------------------------------------------------------------

class TestBuildVentilationFn:
    """Tests for build_ventilation_fn() — the high-level closure builder."""

    def test_returns_callable(self):
        fn = build_ventilation_fn(
            rated_cfm=400, max_static_pressure_inwc=1.5,
            diameter_in=6, length_ft=10, volume_m3=4.32,
        )
        assert callable(fn)

    def test_zero_speed_returns_passive_only(self):
        fn = build_ventilation_fn(
            rated_cfm=400, max_static_pressure_inwc=1.5,
            diameter_in=6, length_ft=10, passive_ach=0.5, volume_m3=4.32,
        )
        ach = fn(0.0)
        assert ach == pytest.approx(0.5)

    def test_full_speed_reasonable_tent_ach(self):
        """A 400 CFM fan in a 4.32 m³ tent should produce 5–50+ ACH."""
        fn = build_ventilation_fn(
            rated_cfm=400, max_static_pressure_inwc=1.5,
            diameter_in=6, length_ft=10, material="flex",
            elbows_90=1, has_carbon_filter=True,
            passive_ach=0.5, volume_m3=4.32,
        )
        ach = fn(1.0)
        assert 5.0 < ach < 200.0

    def test_monotonically_increasing(self):
        fn = build_ventilation_fn(
            rated_cfm=400, max_static_pressure_inwc=1.5,
            diameter_in=6, length_ft=10, volume_m3=4.32,
        )
        speeds = [0.0, 0.25, 0.5, 0.75, 1.0]
        achs = [fn(s) for s in speeds]
        for i in range(1, len(achs)):
            assert achs[i] >= achs[i - 1]

    def test_carbon_filter_reduces_ach(self):
        fn_no_filter = build_ventilation_fn(
            rated_cfm=400, max_static_pressure_inwc=1.5,
            diameter_in=6, length_ft=10,
            has_carbon_filter=False, volume_m3=4.32,
        )
        fn_with_filter = build_ventilation_fn(
            rated_cfm=400, max_static_pressure_inwc=1.5,
            diameter_in=6, length_ft=10,
            has_carbon_filter=True, volume_m3=4.32,
        )
        assert fn_with_filter(1.0) < fn_no_filter(1.0)

    def test_smooth_duct_more_flow_than_flex(self):
        fn_smooth = build_ventilation_fn(
            rated_cfm=400, max_static_pressure_inwc=1.5,
            diameter_in=6, length_ft=10, material="smooth",
            volume_m3=4.32,
        )
        fn_flex = build_ventilation_fn(
            rated_cfm=400, max_static_pressure_inwc=1.5,
            diameter_in=6, length_ft=10, material="flex",
            volume_m3=4.32,
        )
        assert fn_smooth(1.0) > fn_flex(1.0)

    def test_larger_duct_more_flow(self):
        fn_small = build_ventilation_fn(
            rated_cfm=400, max_static_pressure_inwc=1.5,
            diameter_in=4, length_ft=10, volume_m3=4.32,
        )
        fn_large = build_ventilation_fn(
            rated_cfm=400, max_static_pressure_inwc=1.5,
            diameter_in=8, length_ft=10, volume_m3=4.32,
        )
        assert fn_large(1.0) > fn_small(1.0)

    def test_commercial_room_reasonable_ach(self):
        """800 CFM fan in a 300 m³ room — should be 1–15 ACH at full speed."""
        fn = build_ventilation_fn(
            rated_cfm=800, max_static_pressure_inwc=1.8,
            diameter_in=10, length_ft=20, material="smooth",
            elbows_90=2, has_carbon_filter=True,
            passive_ach=0.3, volume_m3=300.0,
        )
        ach = fn(1.0)
        assert 1.0 < ach < 15.0

    def test_warehouse_reasonable_ach(self):
        """4000 CFM fan in a 2400 m³ warehouse — should be 1–10 ACH."""
        fn = build_ventilation_fn(
            rated_cfm=4000, max_static_pressure_inwc=2.5,
            diameter_in=14, length_ft=40, material="smooth",
            elbows_90=3, has_carbon_filter=False,
            passive_ach=0.2, volume_m3=2400.0,
        )
        ach = fn(1.0)
        assert 1.0 < ach < 10.0


# ---------------------------------------------------------------------------
# Unit conversion sanity checks
# ---------------------------------------------------------------------------

class TestUnitConversions:
    """Verify imperial→SI conversion constants."""

    def test_cfm_to_m3s(self):
        # 1 CFM ≈ 0.000472 m³/s
        assert _CFM_TO_M3S == pytest.approx(0.000471947, rel=1e-3)

    def test_inwc_to_pa(self):
        # 1 inWC ≈ 249 Pa
        assert _INWC_TO_PA == pytest.approx(249.089, rel=1e-3)

    def test_inch_to_m(self):
        assert _INCH_TO_M == pytest.approx(0.0254, rel=1e-6)

    def test_ft_to_m(self):
        assert _FT_TO_M == pytest.approx(0.3048, rel=1e-6)
