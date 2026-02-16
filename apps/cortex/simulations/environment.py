"""Physics engine for grow tent environment simulation.

Models correlated physical variables (temperature, humidity, soil moisture,
light intensity) and how actuator operations influence them over time.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Callable


# Actuator ID → internal attribute name mapping
ACTUATOR_MAP: dict[str, str] = {
    "relay1": "fan",
    "relay2": "exhaust_fan",
    "relay3": "humidifier",
    "relay4": "dehumidifier",
    "relay5": "irrigation",
    "relay6": "light",
}

# Per-pot dry-down rate multipliers (slight variance between pots)
_POT_DRY_RATES = [0.045, 0.050, 0.055, 0.048]


def default_ambient_schedule(hour: float) -> float:
    """Sinusoidal day/night ambient temperature cycle.

    Peak (32°C) at 14:00, trough (20°C) at 02:00.
    Mean = 26°C, amplitude = 6°C.
    """
    # Shift so peak is at hour 14 (2pm): sin peaks at π/2, so offset = 14 - 6 = 8
    return 26.0 + 6.0 * math.sin(2 * math.pi * (hour - 8.0) / 24.0)


@dataclass
class GrowTentEnvironment:
    """Simulates a sealed grow tent with interdependent physical variables.

    All rates are expressed per minute and scaled by dt_seconds/60 each step.
    """

    # ── State Variables ───────────────────────────────────────────────
    temperature: float = 24.0
    humidity: float = 55.0
    soil_moisture: list[float] = field(
        default_factory=lambda: [70.0, 68.0, 72.0, 65.0]
    )
    light_intensity: float = 0.0

    # ── Actuator States ───────────────────────────────────────────────
    fan: bool = False
    exhaust_fan: bool = False
    humidifier: bool = False
    dehumidifier: bool = False
    irrigation: bool = False
    light: bool = False

    # ── Ambient Parameters ────────────────────────────────────────────
    ambient_temp: float = 30.0  # outside temperature the tent drifts toward
    ambient_schedule: Callable[[float], float] | None = None  # hour (float) → ambient temp
    noise: bool = True  # add Gaussian sensor noise

    def step(self, dt_seconds: float = 30.0, current_hour: float | None = None) -> None:
        """Advance simulation by dt_seconds.

        Args:
            dt_seconds: Time step in seconds.
            current_hour: Fractional hour of day (0.0-23.99) for ambient schedule.
                          If provided and ambient_schedule is set, overrides ambient_temp.
        """
        dt = dt_seconds / 60.0  # convert to minutes for rate calculations

        # Resolve ambient temperature for this step
        ambient = self.ambient_temp
        if self.ambient_schedule is not None and current_hour is not None:
            ambient = self.ambient_schedule(current_hour)

        # ── 1. Natural Drift ──────────────────────────────────────────
        # Temperature drifts toward ambient
        self.temperature += (ambient - self.temperature) * 0.02 * dt

        # Humidity drifts toward 50% (equilibrium)
        self.humidity += (50.0 - self.humidity) * 0.1 * dt

        # Soil moisture natural dry-down (per pot, varied rates)
        for i in range(len(self.soil_moisture)):
            rate = _POT_DRY_RATES[i % len(_POT_DRY_RATES)]
            self.soil_moisture[i] -= rate * dt

        # Light is 0 unless grow light is on
        if not self.light:
            self.light_intensity = 0.0

        # ── 2. Actuator Effects ───────────────────────────────────────
        if self.fan:
            self.temperature -= 0.1 * dt

        if self.exhaust_fan:
            self.temperature -= 0.3 * dt
            self.humidity -= 0.5 * dt

        if self.humidifier:
            self.humidity += 0.8 * dt

        if self.dehumidifier:
            self.humidity -= 0.6 * dt

        if self.irrigation:
            for i in range(len(self.soil_moisture)):
                self.soil_moisture[i] += 2.0 * dt
            self.humidity += 0.3 * dt

        if self.light:
            self.temperature += 0.15 * dt
            self.light_intensity = 800.0

        # ── 3. Cross-Variable Correlations ────────────────────────────
        # Higher temp accelerates soil evaporation
        temp_excess = self.temperature - 24.0
        if temp_excess > 0:
            for i in range(len(self.soil_moisture)):
                self.soil_moisture[i] -= temp_excess * 0.02 * dt

        # Lower humidity accelerates soil evaporation
        humidity_deficit = 50.0 - self.humidity
        if humidity_deficit > 0:
            for i in range(len(self.soil_moisture)):
                self.soil_moisture[i] -= humidity_deficit * 0.01 * dt

        # Wet soil raises ambient humidity (evapotranspiration)
        avg_soil = sum(self.soil_moisture) / len(self.soil_moisture)
        if avg_soil > 60.0:
            self.humidity += avg_soil * 0.003 * dt

        # ── 4. Sensor Noise ───────────────────────────────────────────
        if self.noise:
            self.temperature += random.gauss(0, 0.15)
            self.humidity += random.gauss(0, 0.3)
            for i in range(len(self.soil_moisture)):
                self.soil_moisture[i] += random.gauss(0, 0.5)
            if self.light:
                self.light_intensity += random.gauss(0, 5.0)

        # ── 5. Clamp to Valid Ranges ──────────────────────────────────
        self.temperature = max(15.0, min(45.0, self.temperature))
        self.humidity = max(20.0, min(95.0, self.humidity))
        for i in range(len(self.soil_moisture)):
            self.soil_moisture[i] = max(0.0, min(100.0, self.soil_moisture[i]))
        self.light_intensity = max(0.0, min(1000.0, self.light_intensity))

    def get_readings(self) -> dict[str, float]:
        """Return current sensor readings as a flat dict."""
        readings = {
            "temp1": round(self.temperature, 2),
            "hum1": round(self.humidity, 2),
            "light1": round(self.light_intensity, 2),
        }
        for i in range(len(self.soil_moisture)):
            readings[f"soil{i + 1}"] = round(self.soil_moisture[i], 2)
        return readings

    def get_actuator_states(self) -> dict[str, bool]:
        """Return current actuator states keyed by friendly name."""
        return {
            "fan": self.fan,
            "exhaust_fan": self.exhaust_fan,
            "humidifier": self.humidifier,
            "dehumidifier": self.dehumidifier,
            "irrigation": self.irrigation,
            "light": self.light,
        }

    def set_actuator(self, target_id: str, value: bool) -> None:
        """Set an actuator by its relay ID (e.g. 'relay1') or friendly name."""
        attr = ACTUATOR_MAP.get(target_id, target_id)
        if hasattr(self, attr) and isinstance(getattr(self, attr), bool):
            setattr(self, attr, bool(value))
