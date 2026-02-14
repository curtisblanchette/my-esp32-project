"""Unit tests for analysis.py — stats, trends, rate-of-change."""

import time

from src.services.analysis import (
    Stats,
    TrendContext,
    calculate_stats,
    calculate_rate_of_change,
    build_trend_context,
    analyze_metric,
    parse_timeframe,
)


class TestCalculateStats:
    def test_empty_values(self):
        stats = calculate_stats([])
        assert stats.mean == 0.0
        assert stats.std_dev == 0.0

    def test_single_value(self):
        stats = calculate_stats([25.0])
        assert stats.mean == 25.0
        assert stats.min == 25.0
        assert stats.max == 25.0
        assert stats.range == 0.0
        assert stats.std_dev == 0.0

    def test_known_values(self):
        stats = calculate_stats([10.0, 20.0, 30.0])
        assert stats.mean == 20.0
        assert stats.min == 10.0
        assert stats.max == 30.0
        assert stats.range == 20.0
        assert stats.std_dev > 0

    def test_uniform_values(self):
        stats = calculate_stats([5.0, 5.0, 5.0])
        assert stats.mean == 5.0
        assert stats.std_dev == 0.0
        assert stats.range == 0.0


class TestCalculateRateOfChange:
    def test_fewer_than_two_values(self):
        assert calculate_rate_of_change([1.0], [1000]) == 0.0
        assert calculate_rate_of_change([], []) == 0.0

    def test_rising_trend(self):
        # 1 degree per minute
        values = [20.0, 21.0, 22.0]
        timestamps = [0, 60_000, 120_000]
        rate = calculate_rate_of_change(values, timestamps)
        assert abs(rate - 1.0) < 0.01

    def test_falling_trend(self):
        values = [30.0, 28.0, 26.0]
        timestamps = [0, 60_000, 120_000]
        rate = calculate_rate_of_change(values, timestamps)
        assert abs(rate - (-2.0)) < 0.01

    def test_stable_trend(self):
        values = [22.0, 22.0, 22.0, 22.0]
        timestamps = [0, 60_000, 120_000, 180_000]
        rate = calculate_rate_of_change(values, timestamps)
        assert rate == 0.0

    def test_same_timestamp(self):
        """All timestamps equal — denominator is zero."""
        values = [20.0, 21.0, 22.0]
        timestamps = [1000, 1000, 1000]
        rate = calculate_rate_of_change(values, timestamps)
        assert rate == 0.0


class TestBuildTrendContext:
    def test_too_few_values(self):
        assert build_trend_context("temperature", [1.0, 2.0], [0, 1000]) is None
        assert build_trend_context("temperature", [], []) is None

    def test_rising_trend(self):
        values = [20.0, 21.0, 22.0, 23.0, 24.0]
        timestamps = [0, 60_000, 120_000, 180_000, 240_000]
        ctx = build_trend_context("temperature", values, timestamps)
        assert ctx is not None
        assert ctx.trend == "rising"
        assert ctx.rate_of_change > 0
        assert ctx.current_value == 24.0
        assert ctx.metric == "temperature"

    def test_falling_trend(self):
        values = [30.0, 28.0, 26.0, 24.0]
        timestamps = [0, 60_000, 120_000, 180_000]
        ctx = build_trend_context("humidity", values, timestamps)
        assert ctx is not None
        assert ctx.trend == "falling"
        assert ctx.rate_of_change < 0

    def test_stable_trend(self):
        values = [22.0, 22.0, 22.0, 22.0, 22.0]
        timestamps = [0, 60_000, 120_000, 180_000, 240_000]
        ctx = build_trend_context("temperature", values, timestamps)
        assert ctx is not None
        assert ctx.trend == "stable"

    def test_noisy_but_stable(self):
        """Small symmetric oscillations around a mean should be stable."""
        values = [22.0, 22.1, 21.9, 22.0, 21.9, 22.1, 22.0]
        timestamps = [0, 60_000, 120_000, 180_000, 240_000, 300_000, 360_000]
        ctx = build_trend_context("temperature", values, timestamps)
        assert ctx is not None
        assert ctx.trend == "stable"


class TestAnalyzeMetric:
    def test_basic_analysis(self):
        values = [20.0, 22.0, 21.0, 23.0, 22.0]
        timestamps = [0, 60_000, 120_000, 180_000, 240_000]
        result = analyze_metric("temperature", values, timestamps)
        assert result.metric == "temperature"
        assert result.stats.mean > 0
        assert result.trend in ("rising", "falling", "stable")

    def test_spike_detection(self):
        """A sudden jump should be detected."""
        values = [22.0, 22.1, 22.0, 35.0, 22.0]
        timestamps = [0, 60_000, 120_000, 180_000, 240_000]
        result = analyze_metric("temperature", values, timestamps)
        spikes = [a for a in result.anomalies if a.type == "spike"]
        assert len(spikes) > 0


class TestParseTimeframe:
    def test_hours(self):
        assert parse_timeframe("24h") == 24 * 60 * 60 * 1000

    def test_days(self):
        assert parse_timeframe("7d") == 7 * 24 * 60 * 60 * 1000

    def test_invalid_returns_default(self):
        assert parse_timeframe("abc") == 24 * 60 * 60 * 1000
