"""Unit tests for forecaster.py — linear forecast, EWMA, breach detection."""

from src.services.forecaster import (
    linear_forecast,
    will_exceed,
    will_drop_below,
    ewma_forecast,
    baseline_deviation,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _timestamps(count: int, interval_ms: int = 60_000, start: int = 0) -> list[int]:
    """Generate evenly spaced timestamps in milliseconds."""
    return [start + i * interval_ms for i in range(count)]


# ── TestLinearForecast ───────────────────────────────────────────────


class TestLinearForecast:
    def test_fewer_than_three_returns_none(self):
        assert linear_forecast([20.0, 21.0], [0, 60_000], 5.0) is None

    def test_empty_returns_none(self):
        assert linear_forecast([], [], 5.0) is None

    def test_rising_forecast(self):
        # 1°C/min rise: 20→21→22→23→24, 1 min apart
        values = [20.0, 21.0, 22.0, 23.0, 24.0]
        ts = _timestamps(5)
        result = linear_forecast(values, ts, 5.0)
        assert result is not None
        assert abs(result - 29.0) < 0.1  # 24 + 5*1.0

    def test_falling_forecast(self):
        # -2°C/min fall: 30→28→26, 1 min apart
        values = [30.0, 28.0, 26.0]
        ts = _timestamps(3)
        result = linear_forecast(values, ts, 3.0)
        assert result is not None
        assert abs(result - 20.0) < 0.1  # 26 + 3*(-2)

    def test_stable_forecast(self):
        values = [22.0, 22.0, 22.0, 22.0]
        ts = _timestamps(4)
        result = linear_forecast(values, ts, 10.0)
        assert result is not None
        assert abs(result - 22.0) < 0.1

    def test_zero_horizon_returns_current(self):
        values = [20.0, 21.0, 22.0]
        ts = _timestamps(3)
        result = linear_forecast(values, ts, 0.0)
        assert result is not None
        assert abs(result - 22.0) < 0.1


# ── TestWillExceed ───────────────────────────────────────────────────


class TestWillExceed:
    def test_rising_toward_threshold(self):
        # rate=1.0/min, current=25, threshold=28 → 3 min, within 5
        values = [23.0, 24.0, 25.0]
        ts = _timestamps(3)
        result = will_exceed(values, ts, 28.0, 5.0)
        assert result.will_breach is True
        assert abs(result.minutes_until_breach - 3.0) < 0.1

    def test_rising_but_too_slow(self):
        # rate=0.1/min, current=25, threshold=28, within=5
        values = [24.8, 24.9, 25.0]
        ts = _timestamps(3)
        result = will_exceed(values, ts, 28.0, 5.0)
        assert result.will_breach is False
        assert result.minutes_until_breach > 5.0

    def test_falling_away_from_threshold(self):
        # rate=-0.5/min, current=25, threshold=28
        values = [26.0, 25.5, 25.0]
        ts = _timestamps(3)
        result = will_exceed(values, ts, 28.0, 15.0)
        assert result.will_breach is False
        assert result.minutes_until_breach == float("inf")

    def test_already_exceeded(self):
        values = [28.0, 29.0, 30.0]
        ts = _timestamps(3)
        result = will_exceed(values, ts, 28.0, 15.0)
        assert result.will_breach is True
        assert result.minutes_until_breach == 0.0

    def test_stable_below_threshold(self):
        values = [25.0, 25.0, 25.0]
        ts = _timestamps(3)
        result = will_exceed(values, ts, 28.0, 15.0)
        assert result.will_breach is False
        assert result.minutes_until_breach == float("inf")

    def test_insufficient_data(self):
        result = will_exceed([25.0, 26.0], [0, 60_000], 28.0, 15.0)
        assert result.will_breach is False
        assert result.minutes_until_breach == float("inf")


# ── TestWillDropBelow ────────────────────────────────────────────────


class TestWillDropBelow:
    def test_falling_toward_threshold(self):
        # rate=-1.0/min, current=22, threshold=18 → 4 min, within 10
        values = [24.0, 23.0, 22.0]
        ts = _timestamps(3)
        result = will_drop_below(values, ts, 18.0, 10.0)
        assert result.will_breach is True
        assert abs(result.minutes_until_breach - 4.0) < 0.1

    def test_falling_but_too_slow(self):
        # rate=-0.1/min, current=22, threshold=18, within=5
        values = [22.2, 22.1, 22.0]
        ts = _timestamps(3)
        result = will_drop_below(values, ts, 18.0, 5.0)
        assert result.will_breach is False
        assert result.minutes_until_breach > 5.0

    def test_rising_away_from_threshold(self):
        values = [21.0, 21.5, 22.0]
        ts = _timestamps(3)
        result = will_drop_below(values, ts, 18.0, 15.0)
        assert result.will_breach is False
        assert result.minutes_until_breach == float("inf")

    def test_already_below(self):
        values = [19.0, 18.0, 17.0]
        ts = _timestamps(3)
        result = will_drop_below(values, ts, 18.0, 15.0)
        assert result.will_breach is True
        assert result.minutes_until_breach == 0.0


# ── TestEwmaForecast ─────────────────────────────────────────────────


class TestEwmaForecast:
    def test_empty_returns_none(self):
        assert ewma_forecast([]) is None

    def test_single_value(self):
        assert ewma_forecast([25.0]) == 25.0

    def test_smoothing_effect(self):
        # [20, 20, 20, 40] with alpha=0.3 — spike at 40 is smoothed
        result = ewma_forecast([20.0, 20.0, 20.0, 40.0], alpha=0.3)
        assert result is not None
        assert result < 30.0  # heavily smoothed below 30
        assert result > 20.0  # but above baseline

    def test_alpha_1_equals_last_value(self):
        result = ewma_forecast([10.0, 20.0, 30.0], alpha=1.0)
        assert result == 30.0

    def test_alpha_near_zero(self):
        result = ewma_forecast([10.0, 20.0, 30.0], alpha=0.01)
        assert result is not None
        assert abs(result - 10.0) < 1.0  # almost first value

    def test_known_sequence(self):
        # Hand-calculated: ewma_0=10, ewma_1=0.5*20+0.5*10=15, ewma_2=0.5*30+0.5*15=22.5
        result = ewma_forecast([10.0, 20.0, 30.0], alpha=0.5)
        assert result is not None
        assert abs(result - 22.5) < 0.01


# ── TestBaselineDeviation ────────────────────────────────────────────


class TestBaselineDeviation:
    def test_at_baseline(self):
        assert baseline_deviation(22.0, 22.0, 1.5) == 0.0

    def test_above_baseline(self):
        result = baseline_deviation(25.0, 22.0, 1.5)
        assert abs(result - 2.0) < 0.01

    def test_below_baseline(self):
        result = baseline_deviation(19.0, 22.0, 1.5)
        assert abs(result - (-2.0)) < 0.01

    def test_zero_std_returns_zero(self):
        assert baseline_deviation(25.0, 22.0, 0.0) == 0.0
