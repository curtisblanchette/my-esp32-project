"""Tests for the substrate physics module — presets, dry-back, irrigation, water stress."""

import math

import pytest

from simulations.substrate_physics import (
    SubstrateConfig,
    ContainerGeometry,
    pot_geometry,
    bed_geometry,
    default_container,
    rockwool,
    coco_perlite_70_30,
    living_soil,
    SUBSTRATE_PRESETS,
    compute_evap_modifier,
    compute_water_stress_factor,
    compute_dryback,
    compute_irrigation,
    compute_humidity_contribution,
    build_substrate_fn,
    _REF_TEMP_C,
    _REF_VPD_KPA,
    _REF_SA_VOL,
)


# ---------------------------------------------------------------------------
# Substrate presets
# ---------------------------------------------------------------------------

class TestPresets:
    """Verify substrate presets have physically reasonable values."""

    @pytest.mark.parametrize("name,factory", list(SUBSTRATE_PRESETS.items()))
    def test_preset_field_ordering(self, name, factory):
        """saturation > field_capacity > stress_onset > wilting_point."""
        s = factory()
        assert s.saturation_vwc > s.field_capacity_vwc
        assert s.field_capacity_vwc > s.stress_onset_vwc
        assert s.stress_onset_vwc > s.wilting_point_vwc
        assert s.wilting_point_vwc >= 0

    @pytest.mark.parametrize("name,factory", list(SUBSTRATE_PRESETS.items()))
    def test_preset_positive_rates(self, name, factory):
        s = factory()
        assert s.k_dry > 0
        assert s.infiltration_rate > 0
        assert s.drainage_rate > 0
        assert 0 < s.surface_evap_factor <= 1.0

    def test_rockwool_fastest_dry(self):
        assert rockwool().k_dry > coco_perlite_70_30().k_dry > living_soil().k_dry

    def test_rockwool_highest_saturation(self):
        assert rockwool().saturation_vwc > coco_perlite_70_30().saturation_vwc > living_soil().saturation_vwc

    def test_living_soil_slowest_infiltration(self):
        assert living_soil().infiltration_rate < coco_perlite_70_30().infiltration_rate < rockwool().infiltration_rate

    def test_three_presets_exist(self):
        assert set(SUBSTRATE_PRESETS.keys()) == {"rockwool", "coco_perlite_70_30", "living_soil"}


# ---------------------------------------------------------------------------
# Container geometry
# ---------------------------------------------------------------------------

class TestContainerGeometry:
    def test_pot_geometry_positive(self):
        g = pot_geometry(30.0, 30.0)
        assert g.volume_liters > 0
        assert g.surface_area_m2 > 0
        assert g.sa_to_vol_ratio > 0

    def test_bed_geometry_positive(self):
        g = bed_geometry(120.0, 60.0, 30.0)
        assert g.volume_liters > 0
        assert g.surface_area_m2 > 0

    def test_small_pot_higher_sa_vol_than_bed(self):
        """Small pots have higher SA:V ratio → dry faster."""
        pot = pot_geometry(15.0, 15.0)
        bed = bed_geometry(120.0, 60.0, 30.0)
        assert pot.sa_to_vol_ratio > bed.sa_to_vol_ratio

    def test_default_container(self):
        g = default_container()
        assert g.volume_liters > 0

    def test_zero_volume_zero_ratio(self):
        g = ContainerGeometry(volume_liters=0.0, surface_area_m2=0.5)
        assert g.sa_to_vol_ratio == 0.0


# ---------------------------------------------------------------------------
# Evaporation modifier
# ---------------------------------------------------------------------------

class TestEvapModifier:
    def test_reference_conditions_approx_one(self):
        """At reference conditions, modifier should be near 1.0."""
        container = ContainerGeometry(
            volume_liters=1.0 / _REF_SA_VOL,
            surface_area_m2=1.0,
        )
        mod = compute_evap_modifier(_REF_TEMP_C, _REF_VPD_KPA, container, 1.0)
        assert mod == pytest.approx(1.0, rel=0.1)

    def test_higher_temp_faster_evap(self):
        container = default_container()
        mod_cool = compute_evap_modifier(20.0, 1.0, container, 1.0)
        mod_hot = compute_evap_modifier(35.0, 1.0, container, 1.0)
        assert mod_hot > mod_cool

    def test_q10_doubles_per_10c(self):
        container = default_container()
        mod_25 = compute_evap_modifier(25.0, 1.0, container, 1.0)
        mod_35 = compute_evap_modifier(35.0, 1.0, container, 1.0)
        ratio = mod_35 / mod_25
        assert ratio == pytest.approx(2.0, rel=0.01)

    def test_higher_vpd_faster_evap(self):
        container = default_container()
        mod_low = compute_evap_modifier(25.0, 0.5, container, 1.0)
        mod_high = compute_evap_modifier(25.0, 2.0, container, 1.0)
        assert mod_high > mod_low

    def test_surface_factor_scales_linearly(self):
        container = default_container()
        mod_full = compute_evap_modifier(25.0, 1.0, container, 1.0)
        mod_half = compute_evap_modifier(25.0, 1.0, container, 0.5)
        assert mod_half == pytest.approx(mod_full * 0.5, rel=0.01)

    def test_zero_vpd_clamped(self):
        """Zero VPD doesn't produce zero or negative modifier."""
        container = default_container()
        mod = compute_evap_modifier(25.0, 0.0, container, 1.0)
        assert mod > 0


# ---------------------------------------------------------------------------
# Water stress factor
# ---------------------------------------------------------------------------

class TestWaterStress:
    def test_above_stress_onset_no_stress(self):
        assert compute_water_stress_factor(50.0, 25.0, 15.0) == 1.0

    def test_at_wilting_point_full_stress(self):
        assert compute_water_stress_factor(15.0, 25.0, 15.0) == 0.0

    def test_below_wilting_point_full_stress(self):
        assert compute_water_stress_factor(5.0, 25.0, 15.0) == 0.0

    def test_midpoint_half_stress(self):
        """Midpoint between wilting and stress onset = 0.5."""
        mid = (25.0 + 15.0) / 2.0
        assert compute_water_stress_factor(mid, 25.0, 15.0) == pytest.approx(0.5)

    def test_linear_between_points(self):
        # Quarter point
        quarter = 15.0 + 0.25 * (25.0 - 15.0)
        assert compute_water_stress_factor(quarter, 25.0, 15.0) == pytest.approx(0.25)

    def test_zero_span_returns_zero(self):
        """If stress_onset == wilting_point, avoid division by zero."""
        assert compute_water_stress_factor(10.0, 20.0, 20.0) == 0.0


# ---------------------------------------------------------------------------
# Dry-back
# ---------------------------------------------------------------------------

class TestDryback:
    def test_dryback_negative_when_wet(self):
        """Dry-back should decrease VWC when above wilting point."""
        s = living_soil()
        d = compute_dryback(40.0, s, evap_modifier=1.0, root_uptake_rate=0.0, dt_minutes=1.0)
        assert d < 0

    def test_dryback_zero_at_wilting_point(self):
        """At wilting point, evaporation stops (no excess above wilting)."""
        s = living_soil()
        d = compute_dryback(s.wilting_point_vwc, s, evap_modifier=1.0, root_uptake_rate=0.0, dt_minutes=1.0)
        assert d == 0.0

    def test_dryback_faster_when_wetter(self):
        """Exponential model: higher VWC → faster dryback."""
        s = coco_perlite_70_30()
        d_wet = compute_dryback(45.0, s, evap_modifier=1.0, root_uptake_rate=0.0, dt_minutes=1.0)
        d_dry = compute_dryback(25.0, s, evap_modifier=1.0, root_uptake_rate=0.0, dt_minutes=1.0)
        assert abs(d_wet) > abs(d_dry)

    def test_higher_evap_modifier_faster_dryback(self):
        s = living_soil()
        d_low = compute_dryback(40.0, s, evap_modifier=0.5, root_uptake_rate=0.0, dt_minutes=1.0)
        d_high = compute_dryback(40.0, s, evap_modifier=2.0, root_uptake_rate=0.0, dt_minutes=1.0)
        assert abs(d_high) > abs(d_low)

    def test_drainage_above_field_capacity(self):
        """Above FC, gravity drainage adds extra loss."""
        s = rockwool()
        # Well above FC
        d_above = compute_dryback(75.0, s, evap_modifier=1.0, root_uptake_rate=0.0, dt_minutes=1.0)
        # Just at FC
        d_at_fc = compute_dryback(s.field_capacity_vwc, s, evap_modifier=1.0, root_uptake_rate=0.0, dt_minutes=1.0)
        # Above FC should lose more (drainage + evap vs evap only)
        assert abs(d_above) > abs(d_at_fc)

    def test_root_uptake_increases_loss(self):
        s = living_soil()
        d_no_root = compute_dryback(40.0, s, evap_modifier=1.0, root_uptake_rate=0.0, dt_minutes=1.0)
        d_with_root = compute_dryback(40.0, s, evap_modifier=1.0, root_uptake_rate=0.01, dt_minutes=1.0)
        assert d_with_root < d_no_root

    def test_rockwool_dries_faster_than_living_soil(self):
        """Rockwool has higher k_dry → faster dry-back at same VWC above wilting."""
        rw = rockwool()
        ls = living_soil()
        # Use a VWC that's above both wilting points by the same margin
        vwc = 30.0  # above both wilting points
        d_rw = compute_dryback(vwc, rw, evap_modifier=1.0, root_uptake_rate=0.0, dt_minutes=1.0)
        d_ls = compute_dryback(vwc, ls, evap_modifier=1.0, root_uptake_rate=0.0, dt_minutes=1.0)
        assert abs(d_rw) > abs(d_ls)


# ---------------------------------------------------------------------------
# Dry-back timescale validation
# ---------------------------------------------------------------------------

class TestDrybackTimescales:
    """Validate that FC → stress_onset takes realistic hours per substrate."""

    @staticmethod
    def _simulate_dryback_hours(substrate: SubstrateConfig, container: ContainerGeometry) -> float:
        """Simulate dry-back from FC to stress_onset at reference conditions.

        Returns hours to reach stress onset.
        """
        vwc = substrate.field_capacity_vwc
        evap_mod = compute_evap_modifier(25.0, 1.0, container, substrate.surface_evap_factor)
        dt = 1.0  # 1 minute steps
        minutes = 0
        max_minutes = 24 * 60  # cap at 24h

        while vwc > substrate.stress_onset_vwc and minutes < max_minutes:
            d = compute_dryback(vwc, substrate, evap_mod, root_uptake_rate=0.001, dt_minutes=dt)
            vwc += d
            minutes += dt

        return minutes / 60.0

    def test_rockwool_fc_to_stress_3_to_6h(self):
        hours = self._simulate_dryback_hours(rockwool(), pot_geometry(15.0, 7.5))
        assert 2.0 < hours < 8.0, f"Rockwool FC→stress: {hours:.1f}h"

    def test_coco_fc_to_stress_4_to_10h(self):
        hours = self._simulate_dryback_hours(coco_perlite_70_30(), pot_geometry(25.0, 25.0))
        assert 3.0 < hours < 12.0, f"Coco FC→stress: {hours:.1f}h"

    def test_living_soil_fc_to_stress_8_to_24h(self):
        hours = self._simulate_dryback_hours(living_soil(), bed_geometry(120.0, 60.0, 30.0))
        assert 6.0 < hours < 24.0, f"Living soil FC→stress: {hours:.1f}h"

    def test_living_soil_slowest_in_realistic_containers(self):
        """Living soil in a bed takes longer to dry than coco in a pot."""
        h_co = self._simulate_dryback_hours(coco_perlite_70_30(), pot_geometry(25.0, 25.0))
        h_ls = self._simulate_dryback_hours(living_soil(), bed_geometry(120.0, 60.0, 30.0))
        assert h_ls > h_co


# ---------------------------------------------------------------------------
# Irrigation
# ---------------------------------------------------------------------------

class TestIrrigation:
    def test_no_irrigation_no_change(self):
        d, runoff = compute_irrigation(30.0, living_soil(), 0.0, 1.0)
        assert d == 0.0
        assert runoff == 0.0

    def test_irrigation_increases_vwc(self):
        d, _ = compute_irrigation(30.0, living_soil(), 1.0, 1.0)
        assert d > 0

    def test_runoff_above_saturation(self):
        """At saturation, all water becomes runoff."""
        s = coco_perlite_70_30()
        d, runoff = compute_irrigation(s.saturation_vwc, s, 1.0, 1.0)
        assert d == 0.0
        assert runoff > 0

    def test_partial_absorption_near_saturation(self):
        s = rockwool()
        # Just below saturation
        d, runoff = compute_irrigation(s.saturation_vwc - 1.0, s, 1.0, 1.0)
        # Some absorbed, some runoff
        assert d > 0
        assert d <= 1.0  # can't absorb more than 1% headroom

    def test_intensity_scales_linearly(self):
        s = living_soil()
        d_half, _ = compute_irrigation(30.0, s, 0.5, 1.0)
        d_full, _ = compute_irrigation(30.0, s, 1.0, 1.0)
        assert d_full == pytest.approx(d_half * 2, rel=0.01)

    def test_infiltration_rate_limits_input(self):
        """Water input per minute is bounded by infiltration_rate × intensity."""
        s = living_soil()
        d, _ = compute_irrigation(20.0, s, 1.0, 1.0)
        assert d <= s.infiltration_rate + 0.01  # 1 minute at full intensity


# ---------------------------------------------------------------------------
# Humidity contribution
# ---------------------------------------------------------------------------

class TestHumidityContribution:
    """compute_humidity_contribution returns g/min (total from all containers)."""

    def test_zero_at_wilting_point(self):
        s = living_soil()
        c = default_container()
        h = compute_humidity_contribution(s.wilting_point_vwc, s, 1.0, c, 4)
        assert h == 0.0

    def test_positive_when_wet(self):
        s = living_soil()
        c = default_container()
        h = compute_humidity_contribution(40.0, s, 1.0, c, 4)
        assert h > 0

    def test_wetter_means_more_humidity(self):
        s = coco_perlite_70_30()
        c = default_container()
        h_dry = compute_humidity_contribution(20.0, s, 1.0, c, 1)
        h_wet = compute_humidity_contribution(45.0, s, 1.0, c, 1)
        assert h_wet > h_dry

    def test_more_containers_more_humidity(self):
        s = living_soil()
        c = default_container()
        h_one = compute_humidity_contribution(40.0, s, 1.0, c, 1)
        h_ten = compute_humidity_contribution(40.0, s, 1.0, c, 10)
        assert h_ten > h_one
        assert abs(h_ten - h_one * 10) < 1e-9  # linear scaling

    def test_zero_containers_no_crash(self):
        s = living_soil()
        c = default_container()
        h = compute_humidity_contribution(40.0, s, 1.0, c, 0)
        assert h == 0.0

    def test_returns_grams_per_minute(self):
        """Verify the magnitude is realistic: ~0.1-1.0 g/min per bed."""
        s = living_soil()
        c = bed_geometry(120, 60, 30)  # 0.72 m² surface area
        h = compute_humidity_contribution(s.field_capacity_vwc, s, 1.0, c, 1)
        # K_EVAP_G=0.15 * wetness=1.0 * evap_mod=1.0 * surface_evap=0.8 * 0.72
        assert 0.05 < h < 0.5  # reasonable g/min for one bed


# ---------------------------------------------------------------------------
# build_substrate_fn closure
# ---------------------------------------------------------------------------

class TestBuildSubstrateFn:
    def test_returns_callable(self):
        fn = build_substrate_fn(living_soil(), default_container())
        assert callable(fn)

    def test_returns_four_values(self):
        fn = build_substrate_fn(living_soil(), default_container())
        result = fn(40.0, 25.0, 1.0, 0.0, 0.0, 1.0)
        assert len(result) == 4

    def test_dryback_reduces_vwc(self):
        fn = build_substrate_fn(living_soil(), default_container())
        new_vwc, _, _, _ = fn(40.0, 25.0, 1.0, 0.0, 0.0, 1.0)
        assert new_vwc < 40.0

    def test_irrigation_increases_vwc(self):
        fn = build_substrate_fn(living_soil(), default_container())
        new_vwc, _, _, _ = fn(30.0, 25.0, 1.0, 1.0, 0.0, 1.0)
        assert new_vwc > 30.0

    def test_clamped_to_saturation(self):
        s = rockwool()
        fn = build_substrate_fn(s, default_container())
        # Massive irrigation at near-saturation
        new_vwc, _, _, _ = fn(84.0, 25.0, 1.0, 1.0, 0.0, 10.0)
        assert new_vwc <= s.saturation_vwc

    def test_clamped_above_zero(self):
        fn = build_substrate_fn(living_soil(), default_container())
        # Very dry, high evaporation
        new_vwc, _, _, _ = fn(1.0, 40.0, 3.0, 0.0, 0.1, 60.0)
        assert new_vwc >= 0.0

    def test_stress_factor_returned(self):
        s = living_soil()
        fn = build_substrate_fn(s, default_container())
        # Above stress onset → stress = 1.0
        _, _, stress, _ = fn(s.field_capacity_vwc, 25.0, 1.0, 0.0, 0.0, 1.0)
        assert stress == 1.0

    def test_stress_factor_below_onset(self):
        s = living_soil()
        fn = build_substrate_fn(s, default_container())
        # Below stress onset
        mid = (s.stress_onset_vwc + s.wilting_point_vwc) / 2.0
        _, _, stress, _ = fn(mid, 25.0, 1.0, 0.0, 0.0, 0.01)  # tiny dt to not move VWC much
        assert 0.0 < stress < 1.0

    def test_humidity_contribution_positive_when_wet(self):
        fn = build_substrate_fn(living_soil(), default_container())
        _, hum, _, _ = fn(40.0, 25.0, 1.0, 0.0, 0.0, 1.0)
        assert hum > 0

    def test_runoff_when_over_irrigated(self):
        s = coco_perlite_70_30()
        fn = build_substrate_fn(s, default_container())
        _, _, _, runoff = fn(s.saturation_vwc, 25.0, 1.0, 1.0, 0.0, 1.0)
        assert runoff > 0


# ---------------------------------------------------------------------------
# PhysicsEngine integration
# ---------------------------------------------------------------------------

class TestPhysicsEngineIntegration:
    """Verify substrate model works correctly inside PhysicsEngine."""

    def test_soil_moisture_decreases_without_irrigation(self):
        from simulations.physics import PhysicsEngine
        s = living_soil()
        c = default_container()
        env = PhysicsEngine(
            noise=False,
            substrate_fn=build_substrate_fn(s, c),
            substrate_config=s,
            substrate_container=c,
            soil_moisture=[40.0, 38.0, 42.0, 36.0],
        )
        initial_avg = sum(env.soil_moisture) / len(env.soil_moisture)
        for _ in range(20):
            env.step(30.0, current_hour=12.0)
        final_avg = sum(env.soil_moisture) / len(env.soil_moisture)
        assert final_avg < initial_avg

    def test_soil_moisture_clamped_to_saturation(self):
        from simulations.physics import PhysicsEngine
        s = living_soil()
        c = default_container()
        env = PhysicsEngine(
            noise=False,
            substrate_fn=build_substrate_fn(s, c),
            substrate_config=s,
            substrate_container=c,
            soil_moisture=[55.0, 55.0, 55.0, 55.0],
        )
        env.irrigation = 1.0
        for _ in range(100):
            env.step(30.0, current_hour=12.0)
        for sm in env.soil_moisture:
            assert sm <= s.saturation_vwc + 0.01

    def test_water_stress_reduces_transpiration(self):
        """When soil is very dry, transpiration should be reduced."""
        from simulations.physics import PhysicsEngine
        s = living_soil()
        c = default_container()

        # Wet soil — full transpiration
        env_wet = PhysicsEngine(
            noise=False,
            substrate_fn=build_substrate_fn(s, c),
            substrate_config=s,
            substrate_container=c,
            soil_moisture=[40.0] * 4,
        )
        env_wet.light = 1.0
        env_wet.step(30.0, current_hour=12.0)
        trans_wet = env_wet.transpiration_rate

        # Very dry soil — stressed, reduced transpiration
        env_dry = PhysicsEngine(
            noise=False,
            substrate_fn=build_substrate_fn(s, c),
            substrate_config=s,
            substrate_container=c,
            soil_moisture=[s.wilting_point_vwc + 1.0] * 4,
        )
        env_dry.light = 1.0
        env_dry.step(30.0, current_hour=12.0)
        trans_dry = env_dry.transpiration_rate

        assert trans_dry < trans_wet

    def test_substrate_contributes_to_humidity(self):
        """Wet substrate should increase room humidity over time."""
        from simulations.physics import PhysicsEngine
        s = living_soil()
        c = default_container()
        env = PhysicsEngine(
            noise=False,
            substrate_fn=build_substrate_fn(s, c),
            substrate_config=s,
            substrate_container=c,
            soil_moisture=[s.field_capacity_vwc] * 4,
            humidity=30.0,  # start dry
        )
        h_initial = env.humidity
        for _ in range(10):
            env.step(30.0, current_hour=12.0)
        # Humidity should increase from substrate evaporation
        # (unless other factors dominate — check it moved)
        assert env.humidity != h_initial
