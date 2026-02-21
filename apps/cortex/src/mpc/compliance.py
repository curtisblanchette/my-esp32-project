"""Real-time compliance tracking for MPC controlled variables.

Tracks how well the controller maintains targets using rolling windows.
Reports compliance percentage, RMSE, and statistics for each variable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


class RingBuffer:
    """Fixed-size ring buffer backed by a numpy array."""

    def __init__(self, capacity: int):
        self._data = np.full(capacity, np.nan)
        self._capacity = capacity
        self._idx = 0
        self._count = 0

    def append(self, value: float) -> None:
        self._data[self._idx] = value
        self._idx = (self._idx + 1) % self._capacity
        self._count = min(self._count + 1, self._capacity)

    def as_array(self) -> np.ndarray:
        """Return filled portion of the buffer in insertion order."""
        if self._count < self._capacity:
            return self._data[:self._count].copy()
        # Wrap-around: data from idx..end, then 0..idx
        return np.concatenate([
            self._data[self._idx:],
            self._data[:self._idx],
        ])

    @property
    def count(self) -> int:
        return self._count

    @property
    def last(self) -> float:
        if self._count == 0:
            return np.nan
        return self._data[(self._idx - 1) % self._capacity]


@dataclass
class ComplianceBand:
    """Target band for a single variable."""
    low: float
    high: float

    @property
    def target(self) -> float:
        return (self.low + self.high) / 2.0


# Default compliance bands (phase-dependent, updated when phase changes)
DEFAULT_BANDS: dict[str, ComplianceBand] = {
    "VPD": ComplianceBand(0.8, 1.4),
    "T_air": ComplianceBand(24.0, 28.0),
    "RH": ComplianceBand(45.0, 65.0),
    "CO2": ComplianceBand(1000.0, 1500.0),
    "theta": ComplianceBand(0.25, 0.45),
    "dryback_rate": ComplianceBand(-0.025, -0.005),
}

# Target compliance levels
TARGET_COMPLIANCE: dict[str, float] = {
    "VPD": 95.0,
    "T_air": 98.0,
    "RH": 90.0,
    "CO2": 92.0,
    "theta": 90.0,
    "dryback_rate": 85.0,
}


class ComplianceTracker:
    """Tracks real-time compliance metrics for MPC controlled variables.

    Maintains rolling windows (default 1 hour at 1 Hz = 3600 samples)
    and computes compliance percentage, RMSE, and statistics.
    """

    def __init__(
        self,
        window: int = 3600,
        bands: dict[str, ComplianceBand] | None = None,
    ):
        self.window = window
        self.bands = bands or dict(DEFAULT_BANDS)
        self.buffers: dict[str, RingBuffer] = {
            key: RingBuffer(window) for key in self.bands
        }

        # Track previous theta for dryback rate calculation
        self._prev_theta: float | None = None
        self._dt: float = 1.0  # update rate in seconds

    def update(
        self,
        x_hat: np.ndarray,
        derived: dict[str, float],
    ) -> None:
        """Record current values. Called every control cycle (1 Hz).

        Args:
            x_hat: State estimate [T_a, w_a, C, T_l, theta, T_s, T_w]
            derived: Derived metrics dict with at least VPD and RH
        """
        values = {
            "VPD": derived.get("VPD", np.nan),
            "T_air": x_hat[0],
            "RH": derived.get("RH", np.nan),
            "CO2": x_hat[2],
            "theta": x_hat[4],
        }

        # Compute dryback rate from theta history
        theta = x_hat[4]
        if self._prev_theta is not None:
            dryback = (theta - self._prev_theta) / self._dt * 3600.0  # m³/m³/hr
            values["dryback_rate"] = dryback
        self._prev_theta = theta

        for key, value in values.items():
            if key in self.buffers and not np.isnan(value):
                self.buffers[key].append(value)

    def report(self) -> dict[str, dict]:
        """Generate compliance report for all tracked variables.

        Returns:
            Dict mapping variable name to compliance metrics.
        """
        report = {}
        for key in self.buffers:
            buf = self.buffers[key]
            if buf.count == 0:
                continue

            data = buf.as_array()
            band = self.bands.get(key)
            if band is None:
                continue

            in_band = np.sum((data >= band.low) & (data <= band.high))
            compliance = float(in_band) / len(data) * 100.0

            rmse = float(np.sqrt(np.mean((data - band.target) ** 2)))

            report[key] = {
                "compliance_pct": round(compliance, 1),
                "rmse": round(rmse, 4),
                "current": round(float(buf.last), 3),
                "target": round(band.target, 3),
                "min": round(float(np.min(data)), 3),
                "max": round(float(np.max(data)), 3),
                "std": round(float(np.std(data)), 4),
                "target_compliance": TARGET_COMPLIANCE.get(key, 90.0),
                "meets_target": compliance >= TARGET_COMPLIANCE.get(key, 90.0),
            }

        return report

    def set_bands(self, bands: dict[str, ComplianceBand]) -> None:
        """Update compliance bands (e.g., when growth phase changes)."""
        self.bands.update(bands)
        # Add buffers for any new keys
        for key in bands:
            if key not in self.buffers:
                self.buffers[key] = RingBuffer(self.window)

    def reset(self) -> None:
        """Clear all buffers."""
        for key in self.buffers:
            self.buffers[key] = RingBuffer(self.window)
        self._prev_theta = None

    def summary(self) -> dict[str, float]:
        """Return overall compliance percentage for each variable."""
        report = self.report()
        return {
            key: metrics["compliance_pct"]
            for key, metrics in report.items()
        }
