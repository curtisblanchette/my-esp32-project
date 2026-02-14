"""
Sensor forecasting utilities for the Cortex decision engine.

Pure functions that project future sensor values from recent history.
Used by the orchestrator's context builder and the decision engine's
forecast condition checks.
"""

import math
from dataclasses import dataclass

from .analysis import calculate_rate_of_change


@dataclass
class ExceedResult:
    """Result of a will-exceed / will-drop-below check."""
    will_breach: bool
    minutes_until_breach: float  # float("inf") if won't breach


def linear_forecast(
    values: list[float],
    timestamps: list[int],
    horizon_minutes: float,
) -> float | None:
    """
    Predict sensor value at now + horizon_minutes using linear regression.

    Reuses the same linear regression math as calculate_rate_of_change()
    but returns the projected value rather than just the slope.

    Returns None if fewer than 3 data points.
    """
    if len(values) < 3 or len(timestamps) < 3:
        return None

    rate = calculate_rate_of_change(values, timestamps)
    return values[-1] + rate * horizon_minutes


def will_exceed(
    values: list[float],
    timestamps: list[int],
    threshold: float,
    within_minutes: float,
) -> ExceedResult:
    """
    Check if the sensor value will exceed a threshold within a time horizon.

    Uses the linear rate of change to project forward. If the trend is
    moving away from the threshold, returns (False, inf).
    """
    if len(values) < 3 or len(timestamps) < 3:
        return ExceedResult(will_breach=False, minutes_until_breach=float("inf"))

    current = values[-1]
    rate = calculate_rate_of_change(values, timestamps)

    # Already past threshold
    if current >= threshold:
        return ExceedResult(will_breach=True, minutes_until_breach=0.0)

    # Moving away or stable
    if rate <= 0:
        return ExceedResult(will_breach=False, minutes_until_breach=float("inf"))

    minutes_to_breach = (threshold - current) / rate
    return ExceedResult(
        will_breach=minutes_to_breach <= within_minutes,
        minutes_until_breach=minutes_to_breach,
    )


def will_drop_below(
    values: list[float],
    timestamps: list[int],
    threshold: float,
    within_minutes: float,
) -> ExceedResult:
    """
    Check if the sensor value will drop below a threshold within a time horizon.

    Mirror of will_exceed for falling values.
    """
    if len(values) < 3 or len(timestamps) < 3:
        return ExceedResult(will_breach=False, minutes_until_breach=float("inf"))

    current = values[-1]
    rate = calculate_rate_of_change(values, timestamps)

    # Already below threshold
    if current <= threshold:
        return ExceedResult(will_breach=True, minutes_until_breach=0.0)

    # Moving away or stable
    if rate >= 0:
        return ExceedResult(will_breach=False, minutes_until_breach=float("inf"))

    minutes_to_breach = (current - threshold) / abs(rate)
    return ExceedResult(
        will_breach=minutes_to_breach <= within_minutes,
        minutes_until_breach=minutes_to_breach,
    )


def ewma_forecast(values: list[float], alpha: float = 0.3) -> float | None:
    """
    Compute exponentially weighted moving average of sensor values.

    More recent values are weighted heavier. Good for smoothing noisy
    sensors like humidity before forecasting.

    Args:
        values: Sensor readings (chronological order).
        alpha: Smoothing factor (0 < alpha <= 1). Higher = more weight on recent.

    Returns None if empty.
    """
    if not values:
        return None

    ewma = values[0]
    for v in values[1:]:
        ewma = alpha * v + (1 - alpha) * ewma
    return ewma


def baseline_deviation(
    current: float,
    baseline_avg: float,
    baseline_std: float,
) -> float:
    """
    How many standard deviations is the current value from baseline?

    Returns 0.0 if std_dev is zero (all baseline values identical).
    """
    if baseline_std == 0:
        return 0.0
    return (current - baseline_avg) / baseline_std
