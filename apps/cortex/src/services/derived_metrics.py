"""
Derived metrics — computed values that don't come from sensors directly.

VPD (Vapor Pressure Deficit) couples temperature and humidity into a single
metric critical for plant transpiration. Dry-back rate tracks soil moisture
change over time. DLI accumulates daily light exposure.

These are the building blocks for ecosystem-level health scoring (Phase 1).
"""

import math


def vpd_from_temp_rh(temp_c: float, rh_pct: float) -> float:
    """
    Compute Vapor Pressure Deficit from temperature and relative humidity.

    Uses the Tetens equation to derive saturation vapor pressure (SVP),
    then VPD = SVP * (1 - RH/100).

    Args:
        temp_c: Temperature in degrees Celsius.
        rh_pct: Relative humidity as a percentage (0-100).

    Returns:
        VPD in kilopascals (kPa). Always >= 0.
    """
    svp = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    vpd = svp * (1 - rh_pct / 100)
    return round(max(0.0, vpd), 3)


def dry_back_rate(
    soil_values: list[float],
    timestamps_ms: list[int],
) -> float | None:
    """
    Compute soil moisture dry-back rate in %/hour.

    Uses linear regression over the provided soil moisture readings.
    Returns the rate of change as a positive number for drying, negative
    for wetting. Returns None if insufficient data (<3 points) or
    zero time span.

    Args:
        soil_values: Soil moisture percentage readings (chronological).
        timestamps_ms: Timestamps in milliseconds (matching soil_values).

    Returns:
        Rate of moisture change in %/hour, or None if insufficient data.
    """
    if len(soil_values) < 3 or len(timestamps_ms) < 3:
        return None

    n = len(soil_values)
    # Convert ms to hours for rate
    hours = [(t - timestamps_ms[0]) / 3_600_000 for t in timestamps_ms]

    if hours[-1] == 0:
        return None

    # Linear regression: slope = rate of change in %/hr
    sum_x = sum(hours)
    sum_y = sum(soil_values)
    sum_xy = sum(x * y for x, y in zip(hours, soil_values))
    sum_x2 = sum(x * x for x in hours)

    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return None

    slope = (n * sum_xy - sum_x * sum_y) / denom
    # Negate so drying is positive (moisture decreasing = positive dry-back)
    return round(-slope, 3)


def daily_light_integral(
    ppfd_values: list[float],
    timestamps_ms: list[int],
) -> float | None:
    """
    Compute Daily Light Integral (DLI) from PPFD sensor readings.

    DLI = integral of PPFD over time, converted from µmol/m²/s to mol/m²/day.
    Uses trapezoidal integration over the provided data window.

    Args:
        ppfd_values: Photosynthetic Photon Flux Density readings (µmol/m²/s).
        timestamps_ms: Timestamps in milliseconds.

    Returns:
        DLI in mol/m²/day, or None if insufficient data.
    """
    if len(ppfd_values) < 2 or len(timestamps_ms) < 2:
        return None

    total_mol = 0.0
    for i in range(1, len(ppfd_values)):
        dt_seconds = (timestamps_ms[i] - timestamps_ms[i - 1]) / 1000
        avg_ppfd = (ppfd_values[i - 1] + ppfd_values[i]) / 2
        # µmol/m²/s * s = µmol/m², convert to mol
        total_mol += avg_ppfd * dt_seconds / 1_000_000

    # Scale to full day (24h) if partial window
    span_hours = (timestamps_ms[-1] - timestamps_ms[0]) / 3_600_000
    if span_hours <= 0:
        return None

    dli_per_day = total_mol * (24.0 / span_hours)
    return round(dli_per_day, 2)


def compute_derived(
    readings: dict[str, float],
    history: dict[str, tuple[list[float], list[int]]] | None = None,
) -> dict[str, float]:
    """
    Compute all derivable metrics from current readings and optional history.

    Args:
        readings: Current sensor values {sensor_id: value}.
        history: Optional per-sensor history {sensor_id: (values, timestamps_ms)}.

    Returns:
        Dict of derived metric values that could be computed from available inputs.
    """
    derived: dict[str, float] = {}

    # VPD — needs temp and humidity
    if "temp1" in readings and "hum1" in readings:
        derived["vpd"] = vpd_from_temp_rh(readings["temp1"], readings["hum1"])

    # Dry-back rate — needs soil moisture history
    if history and "soil1" in history:
        values, timestamps = history["soil1"]
        rate = dry_back_rate(values, timestamps)
        if rate is not None:
            derived["dry_back_rate"] = rate

    # DLI — needs light history
    if history and "light1" in history:
        values, timestamps = history["light1"]
        dli = daily_light_integral(values, timestamps)
        if dli is not None:
            derived["dli"] = dli

    return derived
