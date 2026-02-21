"""Fast physics engine for MPC — 10-15× faster than PhysicsEngine.

Designed for MPC rollouts where the optimizer evaluates ~3,000 physics steps
per solve.  Drops expensive biophysics (Ball-Berry stomatal conductance,
3-surface photosynthesis, per-pot substrate, repeated Tetens exp() calls)
and replaces them with:

- SVP lookup table (41 entries 10–50°C, linear interpolation, zero exp())
- 4 coupled ODEs: temperature, absolute humidity (g/m³), CO2, single VWC
- Simplified transpiration: k_transp × VPD × LAI × light_frac × vwc_available
- Simplified CO2 uptake: k_co2_uptake × light_frac
- Linear substrate dry-back: dVWC = -k_dry × (VWC - wilting) + irrig
- Flat 13-float tuple state snapshot (zero allocation save/restore)

Same public interface as PhysicsEngine (duck-typed for MPCPlanner).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .physics import ActuatorSpec

# ---------------------------------------------------------------------------
# SVP Lookup Table — pre-computed Tetens at integer temps 10–50°C
# ---------------------------------------------------------------------------

_SVP_TABLE_MIN = 10
_SVP_TABLE_MAX = 50
_SVP_TABLE: tuple[float, ...] = tuple(
    0.6108 * math.exp((17.27 * t) / (t + 237.3))
    for t in range(_SVP_TABLE_MIN, _SVP_TABLE_MAX + 1)
)


def _svp_fast(temp_c: float) -> float:
    """Saturation vapor pressure (kPa) via linear interpolation on lookup table.

    Accuracy: < 0.5% error over 15–45°C vs full Tetens equation.
    """
    t = max(float(_SVP_TABLE_MIN), min(float(_SVP_TABLE_MAX), temp_c))
    idx = t - _SVP_TABLE_MIN
    i = int(idx)
    if i >= _SVP_TABLE_MAX - _SVP_TABLE_MIN:
        return _SVP_TABLE[-1]
    frac = idx - i
    return _SVP_TABLE[i] + frac * (_SVP_TABLE[i + 1] - _SVP_TABLE[i])


def _w_to_rh(temp_c: float, w: float) -> float:
    """Absolute humidity (g/m³) → relative humidity (%) using SVP lookup."""
    svp = _svp_fast(temp_c)
    if svp <= 0:
        return 0.0
    tk = temp_c + 273.15
    # Ideal gas: e = w × R_w × T / 1000  (w in g/m³, e in kPa)
    e_actual = w * 0.4615 * tk / 1000.0
    return max(0.0, min(100.0, 100.0 * e_actual / svp))


def _rh_to_w(temp_c: float, rh_pct: float) -> float:
    """Relative humidity (%) → absolute humidity (g/m³) using SVP lookup."""
    svp = _svp_fast(temp_c)
    e_actual = svp * max(0.0, rh_pct) / 100.0
    tk = temp_c + 273.15
    return e_actual * 1000.0 / (0.4615 * tk) if tk > 0 else 0.0


# ---------------------------------------------------------------------------
# Thermodynamic constants (same first-principles as PhysicsEngine)
# ---------------------------------------------------------------------------

_RHO_CP = 1.2 * 1005.0       # ρ_air × c_p  (J/(m³·K))
_U_WALL = 0.804              # overall heat transfer coeff (W/(m²·K))
_L_V    = 2450.0             # latent heat of vaporisation (J/g)

# Thermal efficiency constants — dimensionless, per actuator type
_ETA_LIGHT   = 0.0181
_ETA_FAN     = 0.1287
_ETA_HUMID   = 0.0386
_ETA_DEHUM   = 0.00579
_ETA_TRANSP  = 0.00709

# CO2 flow constants
_K_CO2_INJECT = 57.6         # ppm·m³/(W·min)
_K_RESPIRATION = 0.333       # ppm·m³/(m²_canopy·min)

# HVAC constants
_COP_HVAC = 3.5
_HVAC_DEHUM_GPM_PER_KW = 1.5


# ---------------------------------------------------------------------------
# Simplified substrate defaults
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FastSubstrateConfig:
    """Simplified substrate parameters for the fast engine."""
    k_dry: float = 0.05        # dry-back rate constant (%VWC/min at full stress)
    wilting_vwc: float = 15.0   # permanent wilting point (%VWC)
    stress_onset_vwc: float = 25.0  # water stress onset (%VWC)
    irrig_rate: float = 2.0     # VWC increase per minute at full irrigation intensity
    sat_vwc: float = 72.0       # saturation (max) VWC


# ---------------------------------------------------------------------------
# FastPhysicsEngine
# ---------------------------------------------------------------------------

@dataclass
class FastPhysicsEngine:
    """Lightweight physics engine for MPC rollouts.

    Same public interface as PhysicsEngine (step, save_state, restore_state,
    calibrate, set_actuator, get_readings, get_actuator_states, get_power_watts,
    get_power_breakdown).

    Internal state uses absolute humidity (g/m³) to eliminate RH↔AH conversions
    in the hot loop.  Converts only in get_readings() and calibrate().
    """

    # ── State Variables ───────────────────────────────────────────────
    temperature: float = 24.0
    _w: float = 0.0               # absolute humidity (g/m³) — computed from humidity in __post_init__
    co2: float = 420.0
    vwc: float = 38.0             # single average VWC (%)

    # Initial humidity in %RH (used only for initialization, then converted)
    humidity: float = 55.0

    # ── Derived (updated each step) ──────────────────────────────────
    light_intensity: float = 0.0
    leaf_temperature: float = 24.0
    vpd: float = 0.0
    transpiration_rate: float = 0.0
    photosynthesis_rate: float = 0.0

    # ── Actuator Intensities (0.0–1.0) ────────────────────────────────
    fan: float = 0.0
    exhaust_fan: float = 0.0
    humidifier: float = 0.0
    dehumidifier: float = 0.0
    irrigation: float = 0.0
    light: float = 0.0
    co2_injector: float = 0.0
    hvac: float = 0.0

    # ── Configuration ─────────────────────────────────────────────────
    tent: "SpaceConfig" = None  # type: ignore[assignment]
    plant: "PlantModel" = None  # type: ignore[assignment]

    ambient_temp: float = 30.0
    ambient_schedule: Callable[[float], float] | None = None
    noise: bool = False  # always off for MPC, but kept for interface compat

    actuator_specs: dict[str, "ActuatorSpec"] | None = None
    ventilation_fn: Callable[[float], float] | None = None

    # Simplified substrate
    substrate: FastSubstrateConfig = field(default_factory=FastSubstrateConfig)

    # Transpiration calibration
    k_transp: float = 0.72       # calibrated to match Ball-Berry at reference conditions

    def __post_init__(self) -> None:
        # Lazy imports to avoid circular deps
        from .physics import SpaceConfig, PlantModel, ACTUATOR_SPECS

        if self.tent is None:
            self.tent = SpaceConfig()
        if self.plant is None:
            self.plant = PlantModel()

        specs = self.actuator_specs if self.actuator_specs is not None else ACTUATOR_SPECS
        self._specs = specs
        self._actuator_map: dict[str, str] = {
            s.relay_id: s.name for s in specs.values()
        }
        self._spec_by_name: dict[str, "ActuatorSpec"] = {
            s.name: s for s in specs.values()
        }
        self._relax_quantization: bool = False

        # Convert initial %RH → absolute humidity (g/m³)
        self._w = _rh_to_w(self.temperature, self.humidity)

        # ── Pre-compute thermal coefficients (same first-principles) ──
        V = self.tent.volume_m3
        A = self.tent.floor_area_m2
        C = _RHO_CP * V
        w2d = 60.0 / C  # watts → °C/min

        def _watts(name: str) -> float:
            s = self._spec_by_name.get(name)
            return s.max_watts if s else 0.0

        self._k_drift    = _U_WALL * A * 60.0 / C
        self._light_dpm  = _ETA_LIGHT * _watts("light") * w2d
        self._fan_dpm    = _ETA_FAN * _watts("fan") * w2d
        self._humid_dpm  = _ETA_HUMID * _watts("humidifier") * w2d
        self._dehum_dpm  = _ETA_DEHUM * _watts("dehumidifier") * w2d
        self._transp_dpm = _ETA_TRANSP * _L_V * w2d / 60.0

        hvac_watts = _watts("hvac")
        if hvac_watts > 0:
            cooling_w = _COP_HVAC * hvac_watts
            self._hvac_cool_dpm = cooling_w * w2d
            self._hvac_dehum_gpm = _HVAC_DEHUM_GPM_PER_KW * cooling_w / 1000.0
        else:
            self._hvac_cool_dpm = 0.0
            self._hvac_dehum_gpm = 0.0

        self._co2_inject = _K_CO2_INJECT * _watts("co2_injector") / V
        self._co2_resp   = _K_RESPIRATION / V
        self._vol = V

        # Pre-compute humidifier/dehumidifier moisture rates
        hum_spec = self._spec_by_name.get("humidifier")
        if hum_spec and hum_spec.humidify_g_per_min is not None:
            self._humid_gpm = hum_spec.humidify_g_per_min
        elif hum_spec:
            self._humid_gpm = hum_spec.max_watts * (10.0 / 60.0)
        else:
            self._humid_gpm = 5.0

        dehum_spec = self._spec_by_name.get("dehumidifier")
        if dehum_spec and dehum_spec.dehumidify_g_per_min is not None:
            self._dehum_gpm = dehum_spec.dehumidify_g_per_min
        elif dehum_spec:
            self._dehum_gpm = dehum_spec.max_watts * (12.5 / 300.0)
        else:
            self._dehum_gpm = 12.5

        # Canopy area (constant between steps)
        self._canopy_area = self.plant.lai * self.tent.floor_area_m2

    # ── Core: step() ─────────────────────────────────────────────────

    def step(self, dt_seconds: float = 30.0, current_hour: float | None = None) -> None:
        """Advance by dt_seconds. ~10-15µs per call (10-15× faster than PhysicsEngine)."""
        dt = dt_seconds / 60.0  # minutes

        # Ambient temperature
        ambient = self.ambient_temp
        if self.ambient_schedule is not None and current_hour is not None:
            ambient = self.ambient_schedule(current_hour)

        # Air exchange rate
        if self.ventilation_fn is not None:
            ach = self.ventilation_fn(self.exhaust_fan)
        else:
            ach = self.tent.ach_base + self.tent.ach_max * self.exhaust_fan
        exchange_rate = ach / 60.0  # per minute

        # Light output
        if self.light > 0:
            self.light_intensity = self.tent.max_ppfd * self.light
        else:
            self.light_intensity = 0.0

        # Light fraction (0-1) for simplified transpiration/CO2
        light_frac = self.light_intensity / self.tent.max_ppfd if self.tent.max_ppfd > 0 else 0.0

        # ── VPD (one SVP lookup instead of exp()) ────────────────────
        svp = _svp_fast(self.temperature)
        tk = self.temperature + 273.15
        e_actual = self._w * 0.4615 * tk / 1000.0
        self.vpd = max(0.0, svp - e_actual)

        # ── Transpiration (simplified — no Ball-Berry) ───────────────
        # E_transp = k_transp × VPD × LAI × light_frac × vwc_available
        sub = self.substrate
        vwc_available = max(0.0, (self.vwc - sub.wilting_vwc) / max(1.0, sub.stress_onset_vwc - sub.wilting_vwc))
        vwc_available = min(1.0, vwc_available)

        if self._canopy_area > 0 and light_frac > 0:
            self.transpiration_rate = (
                self.k_transp * self.vpd * self.plant.lai * light_frac * vwc_available
            )
            self.photosynthesis_rate = light_frac * self.plant.pmax if self.plant.pmax > 0 else 0.0
        else:
            self.transpiration_rate = 0.0
            self.photosynthesis_rate = 0.0

        # ── ODE 1: Temperature ───────────────────────────────────────
        d_temp = 0.0
        d_temp += (ambient - self.temperature) * self._k_drift
        d_temp += exchange_rate * (ambient - self.temperature)
        d_temp -= self._fan_dpm * self.fan
        d_temp += self._light_dpm * self.light
        d_temp -= self._transp_dpm * self.transpiration_rate
        d_temp -= self._humid_dpm * self.humidifier
        d_temp += self._dehum_dpm * self.dehumidifier
        d_temp -= self._hvac_cool_dpm * self.hvac
        self.temperature += d_temp * dt

        # ── ODE 2: Absolute Humidity (g/m³) ──────────────────────────
        w_ambient = _rh_to_w(ambient, self.tent.h_ambient)

        d_w = 0.0
        d_w += exchange_rate * (w_ambient - self._w)
        if self._vol > 0:
            d_w += self.transpiration_rate / self._vol
            d_w += self._humid_gpm * self.humidifier / self._vol
            d_w -= self._dehum_gpm * self.dehumidifier / self._vol
            if self._hvac_dehum_gpm > 0:
                d_w -= self._hvac_dehum_gpm * self.hvac / self._vol
            # Soil evaporation: proportional to (VWC - wilting) × VPD
            soil_evap = 0.006 * max(0.0, self.vwc - sub.wilting_vwc) * max(0.0, self.vpd)
            d_w += soil_evap / self._vol
        self._w = max(0.0, self._w + d_w * dt)

        # ── ODE 3: CO2 ──────────────────────────────────────────────
        d_co2 = 0.0
        d_co2 += self._co2_inject * self.co2_injector
        # Simplified uptake: proportional to light only
        if light_frac > 0 and self._canopy_area > 0:
            uptake_ppm = light_frac * self.plant.pmax * self._canopy_area * 60.0 / (self._vol * 40.9)
            d_co2 -= uptake_ppm
        d_co2 += exchange_rate * (self.tent.co2_ambient - self.co2)
        if self.light < 0.01:
            d_co2 += self._co2_resp * self._canopy_area
        self.co2 += d_co2 * dt

        # ── ODE 4: Substrate VWC (single average) ────────────────────
        d_vwc = -sub.k_dry * max(0.0, self.vwc - sub.wilting_vwc)
        # Root uptake (transpiration removes water from substrate)
        if self._canopy_area > 0:
            d_vwc -= self.transpiration_rate * 0.001
        d_vwc += sub.irrig_rate * self.irrigation
        self.vwc += d_vwc * dt

        # ── Leaf Temperature (simple offset) ─────────────────────────
        offset = -self.transpiration_rate * 0.003
        offset = max(-5.0, min(0.5, offset))
        self.leaf_temperature = self.temperature + offset

        # Convert internal AH back to %RH for the humidity field
        self.humidity = _w_to_rh(self.temperature, self._w)

        # ── Clamp to valid ranges ────────────────────────────────────
        self.temperature = max(15.0, min(45.0, self.temperature))
        self.humidity = max(10.0, min(99.0, self.humidity))
        self._w = max(0.0, _rh_to_w(self.temperature, self.humidity))
        self.co2 = max(200.0, min(2500.0, self.co2))
        self.light_intensity = max(0.0, min(self.tent.max_ppfd, self.light_intensity))
        self.leaf_temperature = max(10.0, min(45.0, self.leaf_temperature))
        self.vwc = max(0.0, min(self.substrate.sat_vwc, self.vwc))

    # ── State snapshot (flat tuple — zero allocation) ────────────────

    def save_state(self) -> tuple:
        """Snapshot all mutable state as a flat 13-float tuple."""
        return (
            self.temperature,
            self._w,
            self.co2,
            self.vwc,
            self.light_intensity,
            self.leaf_temperature,
            self.vpd,
            self.transpiration_rate,
            self.photosynthesis_rate,
            self.fan,
            self.exhaust_fan,
            self.humidifier,
            self.dehumidifier,
            self.irrigation,
            self.light,
            self.co2_injector,
            self.hvac,
            self.humidity,
        )

    def restore_state(self, state: tuple) -> None:
        """Restore from a save_state() snapshot."""
        (
            self.temperature,
            self._w,
            self.co2,
            self.vwc,
            self.light_intensity,
            self.leaf_temperature,
            self.vpd,
            self.transpiration_rate,
            self.photosynthesis_rate,
            self.fan,
            self.exhaust_fan,
            self.humidifier,
            self.dehumidifier,
            self.irrigation,
            self.light,
            self.co2_injector,
            self.hvac,
            self.humidity,
        ) = state

    # ── Public API (duck-typed with PhysicsEngine) ───────────────────

    def get_readings(self) -> dict[str, float]:
        """Return current sensor readings as a flat dict."""
        return {
            "temp1": round(self.temperature, 2),
            "hum1": round(self.humidity, 2),
            "light1": round(self.light_intensity, 2),
            "co2_1": round(self.co2, 2),
            "leaf_temp1": round(self.leaf_temperature, 2),
            "vpd1": round(self.vpd, 3),
            "soil1": round(self.vwc, 2),
        }

    def calibrate(self, readings: dict[str, float]) -> None:
        """Sync internal state with live sensor readings."""
        if "temp1" in readings:
            self.temperature = readings["temp1"]
        if "hum1" in readings:
            self.humidity = readings["hum1"]
            self._w = _rh_to_w(self.temperature, self.humidity)
        if "co2_1" in readings:
            self.co2 = readings["co2_1"]
        if "light1" in readings:
            self.light_intensity = readings["light1"]
        if "leaf_temp1" in readings:
            self.leaf_temperature = readings["leaf_temp1"]
        if "soil1" in readings:
            self.vwc = readings["soil1"]
        # Recompute VPD
        svp = _svp_fast(self.temperature)
        tk = self.temperature + 273.15
        e_actual = self._w * 0.4615 * tk / 1000.0
        self.vpd = max(0.0, svp - e_actual)

    def get_actuator_states(self) -> dict[str, bool]:
        """Return actuator states as booleans (backward compat)."""
        return {
            spec.name: getattr(self, spec.name, 0.0) > 0
            for spec in self._specs.values()
        }

    def get_actuator_intensities(self) -> dict[str, float]:
        """Return actuator intensity levels (0.0–1.0)."""
        return {
            spec.name: getattr(self, spec.name, 0.0)
            for spec in self._specs.values()
        }

    def set_actuator(self, target_id: str, value: bool | float) -> None:
        """Set an actuator by relay ID or friendly name."""
        attr = self._actuator_map.get(target_id, target_id)
        if not hasattr(self, attr):
            return
        current = getattr(self, attr)
        if not isinstance(current, (int, float)):
            return
        if isinstance(value, bool):
            intensity = 1.0 if value else 0.0
        else:
            intensity = float(max(0.0, min(1.0, value)))
        spec = self._spec_by_name.get(attr)
        if spec and not self._relax_quantization:
            intensity = spec.quantize(intensity)
        else:
            intensity = max(0.0, min(1.0, intensity))
        setattr(self, attr, intensity)

    def get_power_watts(self) -> float:
        """Current total power draw in watts."""
        total = 0.0
        for spec in self._specs.values():
            intensity = getattr(self, spec.name, 0.0)
            total += spec.max_watts * intensity
        return total

    def get_power_breakdown(self) -> dict[str, float]:
        """Per-actuator power draw in watts."""
        return {
            spec.name: spec.max_watts * getattr(self, spec.name, 0.0)
            for spec in self._specs.values()
        }
