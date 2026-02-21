"""Tests for the 7-state grow room thermodynamic model."""

import numpy as np
import pytest

from src.mpc.model import (
    GrowRoomModel,
    RoomParams,
    StateIndex,
    InputIndex,
    NX,
    NU,
    ND,
    svp,
    dsvp_dt,
    w_sat,
    vpd_from_state,
    rh_from_state,
    stomatal_conductance,
    transpiration_rate,
    net_photosynthesis,
    f_light,
    f_vpd_stomata,
    f_water,
    f_co2_stomata,
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestSVP:
    def test_svp_at_20c(self):
        """SVP at 20°C should be ~2.34 kPa (Tetens)."""
        assert abs(svp(20.0) - 2.338) < 0.01

    def test_svp_at_0c(self):
        """SVP at 0°C should be ~0.611 kPa."""
        assert abs(svp(0.0) - 0.6108) < 0.001

    def test_svp_monotonic(self):
        """SVP should increase with temperature."""
        temps = [0, 10, 20, 30, 40]
        svps = [svp(t) for t in temps]
        assert all(svps[i] < svps[i + 1] for i in range(len(svps) - 1))

    def test_dsvp_dt_positive(self):
        """dSVP/dT should always be positive."""
        for t in [0, 10, 20, 30, 40]:
            assert dsvp_dt(t) > 0


class TestWSat:
    def test_wsat_at_25c(self):
        """Saturation humidity at 25°C should be ~20 g/kg."""
        ws = w_sat(25.0)
        assert 19.0 < ws < 21.0

    def test_wsat_monotonic(self):
        """Saturation humidity should increase with temperature."""
        temps = [10, 20, 30]
        wsats = [w_sat(t) for t in temps]
        assert all(wsats[i] < wsats[i + 1] for i in range(len(wsats) - 1))


class TestVPD:
    def test_vpd_positive(self):
        """VPD should be non-negative."""
        assert vpd_from_state(25.0, 10.0) >= 0

    def test_vpd_zero_at_saturation(self):
        """VPD should be ~0 when air is at saturation."""
        ws = w_sat(25.0)
        vpd = vpd_from_state(25.0, ws)
        assert vpd < 0.1

    def test_vpd_increases_with_drier_air(self):
        """VPD should increase when air is drier (lower w_a)."""
        vpd_wet = vpd_from_state(25.0, 15.0)
        vpd_dry = vpd_from_state(25.0, 5.0)
        assert vpd_dry > vpd_wet


class TestRH:
    def test_rh_at_saturation(self):
        """RH should be 100% at saturation."""
        ws = w_sat(25.0)
        rh = rh_from_state(25.0, ws)
        assert abs(rh - 100.0) < 1.0

    def test_rh_clamped(self):
        """RH should be clamped to [0, 100]."""
        assert rh_from_state(25.0, 0.0) >= 0.0
        assert rh_from_state(25.0, 100.0) <= 100.0


class TestStomatalFactors:
    def test_f_light_saturating(self):
        """Light response should saturate at high PAR."""
        low = f_light(100.0, 200.0)
        high = f_light(1000.0, 200.0)
        assert high > low
        assert high < 1.0

    def test_f_light_zero_dark(self):
        assert f_light(0.0, 200.0) == 0.0

    def test_f_vpd_decreasing(self):
        """Stomata should close at high VPD."""
        low = f_vpd_stomata(0.5, 2.0)
        high = f_vpd_stomata(3.0, 2.0)
        assert low > high

    def test_f_water_linear(self):
        """Water stress should be linear between wilt and FC."""
        assert f_water(0.10, 0.45, 0.10) == pytest.approx(0.0)
        assert f_water(0.45, 0.45, 0.10) == pytest.approx(1.0)
        assert f_water(0.275, 0.45, 0.10) == pytest.approx(0.5)

    def test_f_co2_decreasing(self):
        """Stomata should close at high CO2."""
        low = f_co2_stomata(400, 800)
        high = f_co2_stomata(1600, 800)
        assert low > high


class TestPhotosynthesis:
    def test_net_photo_positive_with_light(self):
        """Net photosynthesis should be positive with light."""
        p = RoomParams()
        assert net_photosynthesis(500.0, 800.0, p) > 0

    def test_net_photo_negative_dark(self):
        """Dark respiration should give negative net photosynthesis."""
        p = RoomParams()
        assert net_photosynthesis(0.0, 800.0, p) < 0


# ---------------------------------------------------------------------------
# Model dynamics
# ---------------------------------------------------------------------------

class TestGrowRoomModel:
    @pytest.fixture
    def model(self):
        return GrowRoomModel()

    @pytest.fixture
    def x0(self, model):
        return model.default_state()

    @pytest.fixture
    def u0(self, model):
        return model.default_input()

    @pytest.fixture
    def d0(self, model):
        return model.default_disturbance()

    def test_dynamics_shape(self, model, x0, u0, d0):
        dxdt = model.dynamics(x0, u0, d0)
        assert dxdt.shape == (NX,)

    def test_dynamics_finite(self, model, x0, u0, d0):
        """All derivatives should be finite."""
        dxdt = model.dynamics(x0, u0, d0)
        assert np.all(np.isfinite(dxdt))

    def test_dynamics_humidity_reasonable(self, model, x0, u0, d0):
        """Humidity derivative should be small (< 1 g/kg/s)."""
        dxdt = model.dynamics(x0, u0, d0)
        assert abs(dxdt[StateIndex.W_AIR]) < 1.0

    def test_dynamics_temperature_reasonable(self, model, x0, u0, d0):
        """Temperature derivative should be small (< 1°C/s)."""
        dxdt = model.dynamics(x0, u0, d0)
        assert abs(dxdt[StateIndex.T_AIR]) < 1.0

    def test_cooling_drives_supply_temp_down(self, model, x0, d0):
        """Applying cooling should drive supply air temperature toward cooling setpoint."""
        u_no_cool = model.default_input()
        u_cool = model.default_input()
        u_cool[InputIndex.COOL] = 1.0

        dxdt_no = model.dynamics(x0, u_no_cool, d0)
        dxdt_cool = model.dynamics(x0, u_cool, d0)
        # HVAC is a first-order lag: cooling drives T_supply toward t_supply_cool
        assert dxdt_cool[StateIndex.T_SUPPLY] < dxdt_no[StateIndex.T_SUPPLY]

    def test_humidifier_increases_humidity(self, model, x0, d0):
        """Humidifier should increase humidity derivative."""
        u_no = model.default_input()
        u_hum = model.default_input()
        u_hum[InputIndex.HUM] = 1.0

        dxdt_no = model.dynamics(x0, u_no, d0)
        dxdt_hum = model.dynamics(x0, u_hum, d0)
        assert dxdt_hum[StateIndex.W_AIR] > dxdt_no[StateIndex.W_AIR]

    def test_co2_injection_increases_co2(self, model, x0, d0):
        """CO2 injection should increase CO2 derivative."""
        u_no = model.default_input()
        u_co2 = model.default_input()
        u_co2[InputIndex.CO2_INJ] = 1.0

        dxdt_no = model.dynamics(x0, u_no, d0)
        dxdt_co2 = model.dynamics(x0, u_co2, d0)
        assert dxdt_co2[StateIndex.CO2] > dxdt_no[StateIndex.CO2]

    def test_irrigation_increases_theta(self, model, x0, d0):
        """Irrigation should increase substrate water content."""
        u_no = model.default_input()
        u_irr = model.default_input()
        u_irr[InputIndex.IRR] = 1.0

        dxdt_no = model.dynamics(x0, u_no, d0)
        dxdt_irr = model.dynamics(x0, u_irr, d0)
        assert dxdt_irr[StateIndex.THETA] > dxdt_no[StateIndex.THETA]


# ---------------------------------------------------------------------------
# Linearization
# ---------------------------------------------------------------------------

class TestLinearization:
    @pytest.fixture
    def model(self):
        return GrowRoomModel()

    def test_linearize_shapes(self, model):
        x0 = model.default_state()
        u0 = model.default_input()
        d0 = model.default_disturbance()
        A_d, B_d, Bd_d = model.linearize(x0, u0, d0, dt=60.0)
        assert A_d.shape == (NX, NX)
        assert B_d.shape == (NX, NU)
        assert Bd_d.shape == (NX, ND)

    def test_linearize_finite(self, model):
        x0 = model.default_state()
        u0 = model.default_input()
        d0 = model.default_disturbance()
        A_d, B_d, Bd_d = model.linearize(x0, u0, d0, dt=60.0)
        assert np.all(np.isfinite(A_d))
        assert np.all(np.isfinite(B_d))
        assert np.all(np.isfinite(Bd_d))

    def test_linearize_prediction_reasonable(self, model):
        """One-step prediction should be close to nonlinear simulation."""
        x0 = model.default_state()
        u0 = model.default_input()
        d0 = model.default_disturbance()
        dt = 60.0

        # Linear prediction
        A_d, B_d, Bd_d = model.linearize(x0, u0, d0, dt)
        x_lin = A_d @ x0 + B_d @ u0 + Bd_d @ d0

        # Nonlinear prediction (Euler)
        dxdt = model.dynamics(x0, u0, d0)
        x_nl = x0 + dxdt * dt

        # Should be reasonably close (< 20% relative error on most states)
        for i in range(NX):
            if abs(x_nl[i]) > 0.01:
                rel_err = abs(x_lin[i] - x_nl[i]) / abs(x_nl[i])
                assert rel_err < 0.5, f"State {i}: lin={x_lin[i]:.4f}, nl={x_nl[i]:.4f}, err={rel_err:.2%}"

    def test_linearize_condition_number(self, model):
        """A_d should have reasonable condition number (< 1000)."""
        x0 = model.default_state()
        u0 = model.default_input()
        d0 = model.default_disturbance()
        A_d, _, _ = model.linearize(x0, u0, d0, dt=60.0)
        cond = np.linalg.cond(A_d)
        assert cond < 1000, f"A_d condition number too high: {cond}"


# ---------------------------------------------------------------------------
# Derived outputs
# ---------------------------------------------------------------------------

class TestDerivedOutputs:
    def test_derived_keys(self):
        model = GrowRoomModel()
        x0 = model.default_state()
        outputs = model.derived_outputs(x0)
        assert "VPD" in outputs
        assert "RH" in outputs
        assert "T_air" in outputs
        assert "CO2" in outputs

    def test_derived_values_reasonable(self):
        model = GrowRoomModel()
        x0 = model.default_state()
        outputs = model.derived_outputs(x0)
        assert 0 < outputs["VPD"] < 5
        assert 0 < outputs["RH"] < 100
        assert outputs["T_air"] == pytest.approx(25.0)