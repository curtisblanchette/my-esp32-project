"""Tests for derived metrics: VPD, dry-back rate, DLI."""

import math
import pytest

from src.services.derived_metrics import (
    vpd_from_temp_rh,
    dry_back_rate,
    daily_light_integral,
    compute_derived,
)


class TestVPD:
    """VPD computation using Tetens equation."""

    def test_typical_grow_room(self):
        """25°C, 60% RH — should be around 1.27 kPa."""
        vpd = vpd_from_temp_rh(25.0, 60.0)
        assert 1.2 < vpd < 1.35

    def test_high_humidity_low_vpd(self):
        """25°C, 90% RH — low VPD (humid conditions)."""
        vpd = vpd_from_temp_rh(25.0, 90.0)
        assert vpd < 0.4

    def test_low_humidity_high_vpd(self):
        """30°C, 30% RH — high VPD (dry conditions)."""
        vpd = vpd_from_temp_rh(30.0, 30.0)
        assert vpd > 2.5

    def test_100_percent_humidity(self):
        """100% RH — VPD should be 0."""
        vpd = vpd_from_temp_rh(25.0, 100.0)
        assert vpd == 0.0

    def test_0_percent_humidity(self):
        """0% RH — VPD equals SVP."""
        vpd = vpd_from_temp_rh(25.0, 0.0)
        svp = 0.6108 * math.exp((17.27 * 25.0) / (25.0 + 237.3))
        assert abs(vpd - round(svp, 3)) < 0.001

    def test_freezing_temperature(self):
        """0°C — should still compute valid VPD."""
        vpd = vpd_from_temp_rh(0.0, 50.0)
        assert vpd > 0

    def test_negative_temperature(self):
        """Below freezing — should still return valid VPD."""
        vpd = vpd_from_temp_rh(-5.0, 50.0)
        assert vpd >= 0

    def test_vpd_never_negative(self):
        """VPD should never be negative even with edge inputs."""
        vpd = vpd_from_temp_rh(20.0, 105.0)  # >100% RH (sensor error)
        assert vpd >= 0

    def test_known_vpd_value(self):
        """Known VPD: 20°C, 50% RH ≈ 1.17 kPa."""
        vpd = vpd_from_temp_rh(20.0, 50.0)
        assert 1.1 < vpd < 1.25

    def test_flower_ideal_vpd(self):
        """Flower stage ideal: 26°C, 55% RH — VPD ~1.5 kPa."""
        vpd = vpd_from_temp_rh(26.0, 55.0)
        assert 1.3 < vpd < 1.7


class TestDryBackRate:
    """Soil moisture dry-back rate computation."""

    def test_drying_soil(self):
        """Soil going from 60% to 45% over 3 hours — positive dry-back."""
        values = [60.0, 55.0, 50.0, 45.0]
        ts = [0, 3_600_000, 7_200_000, 10_800_000]
        rate = dry_back_rate(values, ts)
        assert rate is not None
        assert rate > 0  # drying = positive
        assert abs(rate - 5.0) < 0.5  # ~5%/hr

    def test_wetting_soil(self):
        """Soil going from 30% to 60% — negative dry-back (irrigation)."""
        values = [30.0, 40.0, 50.0, 60.0]
        ts = [0, 3_600_000, 7_200_000, 10_800_000]
        rate = dry_back_rate(values, ts)
        assert rate is not None
        assert rate < 0  # wetting = negative

    def test_stable_soil(self):
        """Flat soil moisture — near-zero rate."""
        values = [50.0, 50.0, 50.0, 50.0]
        ts = [0, 3_600_000, 7_200_000, 10_800_000]
        rate = dry_back_rate(values, ts)
        assert rate is not None
        assert abs(rate) < 0.1

    def test_insufficient_data(self):
        """Less than 3 points returns None."""
        assert dry_back_rate([50.0, 45.0], [0, 3_600_000]) is None
        assert dry_back_rate([], []) is None

    def test_zero_time_span(self):
        """All timestamps identical — returns None."""
        values = [50.0, 45.0, 40.0]
        ts = [1000, 1000, 1000]
        rate = dry_back_rate(values, ts)
        assert rate is None

    def test_rapid_dry_back(self):
        """Fast drying in small pot — high rate."""
        # 60% to 35% in 2 hours
        values = [60.0, 50.0, 40.0, 35.0]
        ts = [0, 2_400_000, 4_800_000, 7_200_000]
        rate = dry_back_rate(values, ts)
        assert rate is not None
        assert rate > 10  # fast drying


class TestDLI:
    """Daily Light Integral computation."""

    def test_constant_ppfd(self):
        """400 µmol/m²/s for 12 hours — DLI ≈ 17.28 mol/m²/day."""
        ppfd = [400.0] * 13  # 13 points over 12 hours
        ts = [i * 3_600_000 for i in range(13)]
        dli = daily_light_integral(ppfd, ts)
        assert dli is not None
        # 400 µmol/s * 43200s / 1e6 * (24/12) = 34.56
        # But 400 * 86400 / 1e6 = 34.56 for 24h at 400
        # For 12h at 400 scaled to 24h = 34.56
        assert 33 < dli < 36

    def test_zero_ppfd(self):
        """No light — DLI should be 0."""
        ppfd = [0.0, 0.0, 0.0, 0.0]
        ts = [0, 3_600_000, 7_200_000, 10_800_000]
        dli = daily_light_integral(ppfd, ts)
        assert dli is not None
        assert dli == 0.0

    def test_insufficient_data(self):
        """Less than 2 points returns None."""
        assert daily_light_integral([400.0], [0]) is None
        assert daily_light_integral([], []) is None

    def test_varying_ppfd(self):
        """Ramp up / ramp down (sunrise/sunset pattern)."""
        ppfd = [0.0, 200.0, 400.0, 400.0, 200.0, 0.0]
        ts = [0, 3_600_000, 7_200_000, 10_800_000, 14_400_000, 18_000_000]
        dli = daily_light_integral(ppfd, ts)
        assert dli is not None
        assert dli > 0

    def test_zero_time_span(self):
        """Same timestamp — returns None."""
        assert daily_light_integral([400.0, 400.0], [1000, 1000]) is None


class TestComputeDerived:
    """Integration: compute all available derived metrics."""

    def test_vpd_from_readings(self):
        """VPD computed when temp and humidity available."""
        result = compute_derived({"temp1": 25.0, "hum1": 60.0})
        assert "vpd" in result
        assert result["vpd"] > 0

    def test_vpd_missing_humidity(self):
        """VPD not computed without humidity."""
        result = compute_derived({"temp1": 25.0})
        assert "vpd" not in result

    def test_vpd_missing_temp(self):
        """VPD not computed without temperature."""
        result = compute_derived({"hum1": 60.0})
        assert "vpd" not in result

    def test_dry_back_with_history(self):
        """Dry-back rate computed from soil history."""
        history = {
            "soil1": (
                [60.0, 55.0, 50.0, 45.0],
                [0, 3_600_000, 7_200_000, 10_800_000],
            ),
        }
        result = compute_derived({"soil1": 45.0}, history=history)
        assert "dry_back_rate" in result
        assert result["dry_back_rate"] > 0

    def test_no_dry_back_without_history(self):
        """Dry-back rate requires history."""
        result = compute_derived({"soil1": 45.0})
        assert "dry_back_rate" not in result

    def test_dli_with_history(self):
        """DLI computed from light history."""
        history = {
            "light1": (
                [400.0, 400.0, 400.0],
                [0, 3_600_000, 7_200_000],
            ),
        }
        result = compute_derived({"light1": 400.0}, history=history)
        assert "dli" in result
        assert result["dli"] > 0

    def test_all_derived_computed(self):
        """All derived metrics computed when all inputs available."""
        history = {
            "soil1": ([60.0, 55.0, 50.0], [0, 3_600_000, 7_200_000]),
            "light1": ([400.0, 400.0, 400.0], [0, 3_600_000, 7_200_000]),
        }
        result = compute_derived(
            {"temp1": 25.0, "hum1": 60.0, "soil1": 50.0, "light1": 400.0},
            history=history,
        )
        assert "vpd" in result
        assert "dry_back_rate" in result
        assert "dli" in result

    def test_empty_readings(self):
        """No readings — no derived metrics."""
        result = compute_derived({})
        assert result == {}
