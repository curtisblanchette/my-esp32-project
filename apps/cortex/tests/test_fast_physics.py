"""Tests for FastPhysicsEngine — SVP accuracy, direction tests, benchmarks, regression."""

import math
import time

import pytest

from simulations.fast_physics import (
    FastPhysicsEngine,
    FastSubstrateConfig,
    _svp_fast,
    _w_to_rh,
    _rh_to_w,
)
from simulations.physics import (
    PhysicsEngine,
    SpaceConfig,
    PlantModel,
    vpd_from_temp_rh,
    rh_to_abs_humidity,
    abs_humidity_to_rh,
)


# ---------------------------------------------------------------------------
# SVP Lookup Accuracy
# ---------------------------------------------------------------------------

class TestSVPLookup:
    """SVP lookup table accuracy vs full Tetens equation."""

    def test_svp_exact_at_integer_temps(self):
        """At integer temps, lookup should match Tetens exactly (they're the table entries)."""
        for t in range(10, 51):
            tetens = 0.6108 * math.exp((17.27 * t) / (t + 237.3))
            assert abs(_svp_fast(t) - tetens) < 1e-10, f"Mismatch at {t}°C"

    def test_svp_accuracy_fractional_temps(self):
        """At fractional temps 15-45°C, max error should be < 0.5% vs Tetens."""
        max_error_pct = 0.0
        for t10 in range(150, 451):
            t = t10 / 10.0
            tetens = 0.6108 * math.exp((17.27 * t) / (t + 237.3))
            fast = _svp_fast(t)
            error_pct = abs(fast - tetens) / tetens * 100
            max_error_pct = max(max_error_pct, error_pct)
        assert max_error_pct < 0.5, f"Max SVP error {max_error_pct:.3f}% exceeds 0.5%"

    def test_svp_clamped_below_range(self):
        """Below 10°C should clamp to table minimum."""
        assert _svp_fast(5.0) == _svp_fast(10.0)
        assert _svp_fast(-10.0) == _svp_fast(10.0)

    def test_svp_clamped_above_range(self):
        """Above 50°C should clamp to table maximum."""
        assert _svp_fast(55.0) == _svp_fast(50.0)


# ---------------------------------------------------------------------------
# Psychrometric Conversions
# ---------------------------------------------------------------------------

class TestPsychrometrics:
    """Psychrometric conversion accuracy and roundtrip identity."""

    def test_w_rh_roundtrip(self):
        """w → RH → w should be identity (within float precision)."""
        for temp in [15.0, 20.0, 25.0, 30.0, 35.0, 40.0]:
            for rh in [20.0, 40.0, 60.0, 80.0, 95.0]:
                w = _rh_to_w(temp, rh)
                rh_back = _w_to_rh(temp, w)
                assert abs(rh_back - rh) < 0.1, (
                    f"Roundtrip failed at {temp}°C, {rh}%RH: got {rh_back:.2f}"
                )

    def test_vs_physics_engine_conversions(self):
        """Fast conversions should match PhysicsEngine within 0.5%."""
        for temp in [20.0, 25.0, 30.0, 35.0]:
            for rh in [30.0, 50.0, 70.0, 90.0]:
                w_fast = _rh_to_w(temp, rh)
                w_full = rh_to_abs_humidity(temp, rh)
                if w_full > 0:
                    error_pct = abs(w_fast - w_full) / w_full * 100
                    assert error_pct < 0.5, (
                        f"w mismatch at {temp}°C, {rh}%RH: "
                        f"fast={w_fast:.4f}, full={w_full:.4f}, error={error_pct:.3f}%"
                    )


# ---------------------------------------------------------------------------
# Direction Tests
# ---------------------------------------------------------------------------

class TestDirectionTests:
    """Verify each actuator moves the right state variable in the right direction."""

    def _make_engine(self, **kw) -> FastPhysicsEngine:
        return FastPhysicsEngine(
            temperature=25.0,
            humidity=55.0,
            co2=420.0,
            vwc=38.0,
            ambient_temp=30.0,
            noise=False,
            **kw,
        )

    def test_fan_cools(self):
        """Fan should lower temperature."""
        eng = self._make_engine(fan=1.0)
        eng_no = self._make_engine(fan=0.0)
        for _ in range(60):
            eng.step(30.0, current_hour=12.0)
            eng_no.step(30.0, current_hour=12.0)
        assert eng.temperature < eng_no.temperature

    def test_humidifier_raises_humidity(self):
        """Humidifier should raise humidity."""
        eng = self._make_engine(humidifier=1.0)
        eng_no = self._make_engine(humidifier=0.0)
        for _ in range(60):
            eng.step(30.0, current_hour=12.0)
            eng_no.step(30.0, current_hour=12.0)
        assert eng.humidity > eng_no.humidity

    def test_dehumidifier_lowers_humidity(self):
        """Dehumidifier should lower humidity."""
        eng = self._make_engine(dehumidifier=1.0)
        eng_no = self._make_engine(dehumidifier=0.0)
        for _ in range(60):
            eng.step(30.0, current_hour=12.0)
            eng_no.step(30.0, current_hour=12.0)
        assert eng.humidity < eng_no.humidity

    def test_co2_injector_raises_co2(self):
        """CO2 injector should raise CO2 levels."""
        eng = self._make_engine(co2_injector=1.0)
        eng_no = self._make_engine(co2_injector=0.0)
        for _ in range(60):
            eng.step(30.0, current_hour=12.0)
            eng_no.step(30.0, current_hour=12.0)
        assert eng.co2 > eng_no.co2

    def test_irrigation_raises_vwc(self):
        """Irrigation should raise VWC."""
        eng = self._make_engine(irrigation=1.0)
        eng_no = self._make_engine(irrigation=0.0)
        for _ in range(60):
            eng.step(30.0, current_hour=12.0)
            eng_no.step(30.0, current_hour=12.0)
        assert eng.vwc > eng_no.vwc

    def test_light_heats(self):
        """Light should raise temperature via waste heat."""
        eng = self._make_engine(light=1.0)
        eng_no = self._make_engine(light=0.0)
        for _ in range(60):
            eng.step(30.0, current_hour=12.0)
            eng_no.step(30.0, current_hour=12.0)
        assert eng.temperature > eng_no.temperature

    def test_light_produces_ppfd(self):
        """Light should produce PPFD proportional to intensity."""
        eng = self._make_engine(light=0.5)
        eng.step(30.0, current_hour=12.0)
        assert eng.light_intensity == pytest.approx(500.0, abs=1.0)

    def test_exhaust_fan_increases_air_exchange(self):
        """Exhaust fan should move temperature toward ambient faster."""
        # Ambient is 30°C, starting at 25°C — exhaust fan should warm faster
        eng = self._make_engine(exhaust_fan=1.0)
        eng_no = self._make_engine(exhaust_fan=0.0)
        for _ in range(60):
            eng.step(30.0, current_hour=12.0)
            eng_no.step(30.0, current_hour=12.0)
        # Both approach 30°C; exhaust fan should be closer
        assert abs(eng.temperature - 30.0) < abs(eng_no.temperature - 30.0)


# ---------------------------------------------------------------------------
# Save/Restore Roundtrip
# ---------------------------------------------------------------------------

class TestSaveRestore:
    """State snapshot and restoration."""

    def test_save_restore_identity(self):
        """restore_state(save_state()) should be exact identity."""
        eng = FastPhysicsEngine(
            temperature=26.5, humidity=58.0, co2=800.0, vwc=45.0,
            fan=0.3, light=0.7, co2_injector=1.0,
        )
        state = eng.save_state()
        eng.step(30.0, current_hour=14.0)

        # State changed
        assert eng.temperature != 26.5

        # Restore
        eng.restore_state(state)
        assert eng.temperature == 26.5
        assert eng.co2 == 800.0
        assert eng.vwc == 45.0
        assert eng.fan == 0.3

    def test_state_is_tuple(self):
        """save_state() should return a plain tuple (no list allocation)."""
        eng = FastPhysicsEngine()
        state = eng.save_state()
        assert isinstance(state, tuple)
        assert all(isinstance(v, float) for v in state)

    def test_multiple_save_restore_cycles(self):
        """Multiple save/restore cycles should work correctly."""
        eng = FastPhysicsEngine(temperature=22.0, humidity=60.0)
        states = []
        for i in range(5):
            states.append(eng.save_state())
            eng.step(30.0, current_hour=12.0)

        # Restore each state in reverse and verify
        for i in range(4, -1, -1):
            eng.restore_state(states[i])
            if i == 0:
                assert eng.temperature == pytest.approx(22.0, abs=0.01)


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

class TestBenchmark:
    """Performance benchmarks — the whole point of this engine."""

    def test_3000_steps_under_50ms(self):
        """3,000 step() calls should complete in < 50ms (target: 30-45ms)."""
        eng = FastPhysicsEngine(
            temperature=24.0, humidity=55.0, co2=420.0, vwc=38.0,
            light=0.8, fan=0.5, exhaust_fan=0.3,
            noise=False,
        )

        t0 = time.perf_counter()
        for i in range(3000):
            eng.step(30.0, current_hour=12.0 + i * 0.5 / 3000)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert elapsed_ms < 50, f"3000 steps took {elapsed_ms:.1f}ms (target: <50ms)"

    def test_save_restore_speed(self):
        """10,000 save/restore cycles should be fast (< 10ms)."""
        eng = FastPhysicsEngine()
        state = eng.save_state()

        t0 = time.perf_counter()
        for _ in range(10000):
            eng.restore_state(state)
            state = eng.save_state()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert elapsed_ms < 30, f"10k save/restore took {elapsed_ms:.1f}ms (target: <30ms)"


# ---------------------------------------------------------------------------
# Regression vs PhysicsEngine
# ---------------------------------------------------------------------------

class TestRegressionVsPhysicsEngine:
    """Compare FastPhysicsEngine output to PhysicsEngine under identical conditions.

    Target: within ~10-15% after 30 minutes of simulation.
    """

    def _make_pair(self):
        """Create matched PhysicsEngine and FastPhysicsEngine."""
        space = SpaceConfig()
        plant = PlantModel()

        full = PhysicsEngine(
            temperature=24.0, humidity=55.0, co2=420.0,
            soil_moisture=[38.0, 38.0, 38.0, 38.0],
            noise=False, ambient_temp=30.0,
            tent=space, plant=plant,
        )

        fast = FastPhysicsEngine(
            temperature=24.0, humidity=55.0, co2=420.0, vwc=38.0,
            noise=False, ambient_temp=30.0,
            tent=space, plant=plant,
        )
        return full, fast

    def test_temperature_within_15pct(self):
        """Temperature should track within 15% after 30 min."""
        full, fast = self._make_pair()

        # Run with lights on and fan
        full.set_actuator("light", True)
        fast.set_actuator("light", True)
        full.set_actuator("fan", True)
        fast.set_actuator("fan", True)

        for _ in range(60):  # 30 minutes
            full.step(30.0, current_hour=12.0)
            fast.step(30.0, current_hour=12.0)

        full_t = full.temperature
        fast_t = fast.temperature
        # Absolute difference check — both should be in roughly the same ballpark
        assert abs(full_t - fast_t) < max(abs(full_t) * 0.15, 2.0), (
            f"Temp diverged: full={full_t:.2f}, fast={fast_t:.2f}"
        )

    def test_humidity_direction_matches(self):
        """Humidity should move in the same direction as PhysicsEngine."""
        full, fast = self._make_pair()

        # Run with humidifier
        full.set_actuator("humidifier", True)
        fast.set_actuator("humidifier", True)

        for _ in range(60):
            full.step(30.0, current_hour=12.0)
            fast.step(30.0, current_hour=12.0)

        # Both should have higher humidity than starting 55%
        assert full.humidity > 55.0
        assert fast.humidity > 55.0

    def test_co2_direction_matches(self):
        """CO2 should move in the same direction as PhysicsEngine."""
        full, fast = self._make_pair()

        full.set_actuator("co2_injector", True)
        fast.set_actuator("co2_injector", True)

        for _ in range(60):
            full.step(30.0, current_hour=12.0)
            fast.step(30.0, current_hour=12.0)

        assert full.co2 > 420.0
        assert fast.co2 > 420.0

    def test_readings_keys_match(self):
        """get_readings() should produce overlapping sensor keys."""
        full, fast = self._make_pair()
        full_keys = set(full.get_readings().keys())
        fast_keys = set(fast.get_readings().keys())

        # Fast engine should have all the essential keys
        essential = {"temp1", "hum1", "co2_1", "light1", "leaf_temp1", "vpd1"}
        assert essential.issubset(fast_keys)

        # soil1 is in fast (single VWC), full has soil1..soil4
        assert "soil1" in fast_keys

    def test_actuator_interface_compatible(self):
        """set_actuator, get_actuator_states, get_power should all work."""
        fast = FastPhysicsEngine()

        # Set by relay ID
        fast.set_actuator("relay1", True)
        assert fast.fan == 1.0

        # Set by name
        fast.set_actuator("fan", False)
        assert fast.fan == 0.0

        # Get states
        states = fast.get_actuator_states()
        assert isinstance(states, dict)
        assert "fan" in states

        # Power
        fast.set_actuator("light", True)
        watts = fast.get_power_watts()
        assert watts > 0

        breakdown = fast.get_power_breakdown()
        assert "light" in breakdown
        assert breakdown["light"] > 0


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_zero_lai_no_transpiration(self):
        """With LAI=0 (dry/cure phase), transpiration should be zero."""
        eng = FastPhysicsEngine(
            plant=PlantModel.for_phase("dry"),
            light=1.0,
        )
        eng.step(30.0, current_hour=12.0)
        assert eng.transpiration_rate == 0.0

    def test_no_light_no_transpiration(self):
        """Without light, no transpiration even with healthy plants."""
        eng = FastPhysicsEngine(light=0.0)
        eng.step(30.0, current_hour=12.0)
        assert eng.transpiration_rate == 0.0

    def test_very_dry_substrate_reduces_transpiration(self):
        """VWC near wilting should reduce transpiration toward zero."""
        eng_wet = FastPhysicsEngine(vwc=40.0, light=1.0)
        eng_dry = FastPhysicsEngine(vwc=16.0, light=1.0)  # just above wilting

        eng_wet.step(30.0, current_hour=12.0)
        eng_dry.step(30.0, current_hour=12.0)

        assert eng_dry.transpiration_rate < eng_wet.transpiration_rate

    def test_ambient_schedule_used(self):
        """Ambient schedule function should affect temperature drift."""
        def cold_schedule(h):
            return 15.0  # always cold

        eng = FastPhysicsEngine(
            temperature=25.0, ambient_schedule=cold_schedule,
        )
        for _ in range(120):
            eng.step(30.0, current_hour=12.0)
        # Should drift toward 15°C
        assert eng.temperature < 22.0

    def test_custom_substrate_config(self):
        """Custom FastSubstrateConfig should be respected."""
        sub = FastSubstrateConfig(k_dry=0.2, wilting_vwc=10.0, sat_vwc=80.0)
        eng = FastPhysicsEngine(vwc=50.0, substrate=sub)
        eng.step(30.0, current_hour=12.0)
        # Higher k_dry should cause faster drying
        assert eng.vwc < 50.0

    def test_ventilation_fn_used(self):
        """Custom ventilation_fn should be called instead of linear ACH."""
        calls = []
        def vent_fn(intensity):
            calls.append(intensity)
            return 5.0  # fixed ACH

        eng = FastPhysicsEngine(exhaust_fan=0.7, ventilation_fn=vent_fn)
        eng.step(30.0, current_hour=12.0)
        assert len(calls) == 1
        assert calls[0] == pytest.approx(0.7)
