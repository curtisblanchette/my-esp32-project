"""Physics engine for environment simulation.

Models correlated physical variables (temperature, humidity, soil moisture,
light/PPFD, CO2, leaf temperature) and how actuator operations influence them.

Actuators support continuous intensity (0.0–1.0). Boolean True/False maps to
1.0/0.0 for backward compatibility with the existing rule-based decision engine.

Plant physiology: simplified Ball-Berry stomatal conductance, rectangular
hyperbola light response, Michaelis-Menten CO2 response, Gaussian temperature
optimum. Based on Chandra et al. 2008/2011 cannabis photosynthesis data.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .substrate_physics import ContainerGeometry, SubstrateConfig


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ActuatorSpec:
    """Specification for a single actuator.

    control_type determines how the actuator responds to intensity values:
      - "binary":   on/off relay — snaps to 0.0 or 1.0 (default)
      - "variable": 0-10V / PWM continuous — any value 0.0–1.0
    """
    name: str               # friendly name: "fan", "exhaust_fan", etc.
    relay_id: str           # "relay1", "relay2", etc.
    max_watts: float        # peak power consumption in watts
    control_type: str = "binary"  # "binary" | "variable"
    humidify_g_per_min: float | None = None    # g H₂O/min at full intensity
    dehumidify_g_per_min: float | None = None  # g H₂O/min at full intensity

    def quantize(self, value: float) -> float:
        """Constrain a raw 0.0–1.0 intensity to this actuator's control type."""
        v = max(0.0, min(1.0, float(value)))
        if self.control_type == "binary":
            return 1.0 if v >= 0.5 else 0.0
        return v  # variable — pass through clamped


@dataclass
class PlantModel:
    """Configurable plant physiology parameters."""
    lai: float = 3.0              # Leaf Area Index (total leaf area / ground area)
    phase: str = "veg"            # seedling / veg / flower / late_flower / dry / cure
    pmax: float = 25.0            # max assimilation rate (µmol CO2/m²/s)
    alpha: float = 0.06           # quantum yield (µmol CO2 per µmol photons)
    t_opt: float = 28.0           # optimal photosynthesis temperature (°C)
    t_sigma: float = 8.0          # temperature response width parameter
    km_co2: float = 300.0         # CO2 half-saturation constant (ppm)
    g0: float = 0.01              # minimum stomatal conductance (mol/m²/s)
    m_bb: float = 8.0             # Ball-Berry slope parameter

    @classmethod
    def for_phase(cls, phase: str) -> "PlantModel":
        """Return a PlantModel with phase-appropriate defaults."""
        presets = {
            "seedling":    cls(lai=0.5,  phase=phase, pmax=10.0, g0=0.005, m_bb=6.0),
            "veg":         cls(lai=3.0,  phase=phase, pmax=25.0, g0=0.01,  m_bb=8.0),
            "flower":      cls(lai=4.5,  phase=phase, pmax=30.0, g0=0.012, m_bb=9.0),
            "late_flower":  cls(lai=4.0,  phase=phase, pmax=22.0, g0=0.008, m_bb=7.0),
            "dry":         cls(lai=0.0,  phase=phase, pmax=0.0,  g0=0.0,   m_bb=0.0),
            "cure":        cls(lai=0.0,  phase=phase, pmax=0.0,  g0=0.0,   m_bb=0.0),
        }
        return presets.get(phase, cls(phase=phase))


@dataclass
class SpaceConfig:
    """Physical space parameters (tent, room, warehouse, etc.)."""
    floor_area_m2: float = 1.44     # 1.2m × 1.2m
    volume_m3: float = 2.88         # 1.2 × 1.2 × 2.0m
    ach_base: float = 0.5           # base air changes/hour (infiltration, leaks)
    ach_max: float = 20.0           # max ACH at full exhaust fan
    num_pots: int = 4
    max_ppfd: float = 1000.0        # max light output µmol/m²/s
    co2_ambient: float = 420.0      # outdoor CO2 ppm
    h_ambient: float = 50.0         # outdoor humidity %


# ---------------------------------------------------------------------------
# Actuator specifications
# ---------------------------------------------------------------------------

ACTUATOR_SPECS: dict[str, ActuatorSpec] = {
    "relay1": ActuatorSpec(name="fan",           relay_id="relay1", max_watts=45),
    "relay2": ActuatorSpec(name="exhaust_fan",   relay_id="relay2", max_watts=85),
    "relay3": ActuatorSpec(name="humidifier",    relay_id="relay3", max_watts=30,
                           humidify_g_per_min=5.0),
    "relay4": ActuatorSpec(name="dehumidifier",  relay_id="relay4", max_watts=300,
                           dehumidify_g_per_min=12.5),
    "relay5": ActuatorSpec(name="irrigation",    relay_id="relay5", max_watts=15),
    "relay6": ActuatorSpec(name="light",         relay_id="relay6", max_watts=480),
    "relay7": ActuatorSpec(name="co2_injector",  relay_id="relay7", max_watts=10),
}

# Backward-compat flat map: relay ID → friendly name
ACTUATOR_MAP: dict[str, str] = {
    spec.relay_id: spec.name for spec in ACTUATOR_SPECS.values()
}

# Reverse lookup: friendly name → spec (for quantization in set_actuator)
_SPEC_BY_NAME: dict[str, ActuatorSpec] = {
    spec.name: spec for spec in ACTUATOR_SPECS.values()
}

def default_ambient_schedule(hour: float) -> float:
    """Sinusoidal day/night ambient temperature cycle.

    Peak (32°C) at 14:00, trough (20°C) at 02:00.
    Mean = 26°C, amplitude = 6°C.
    """
    return 26.0 + 6.0 * math.sin(2 * math.pi * (hour - 8.0) / 24.0)


def vpd_from_temp_rh(temp_c: float, rh_pct: float) -> float:
    """Compute vapor pressure deficit using the Tetens equation.

    Returns VPD in kPa. Inlined here so the physics engine has no
    dependency on derived_metrics (which lives in src/).
    """
    svp = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    return max(0.0, svp * (1.0 - rh_pct / 100.0))


# ---------------------------------------------------------------------------
# Psychrometric conversions
# ---------------------------------------------------------------------------

_R_W = 0.4615  # specific gas constant for water vapor (kPa·m³/(kg·K))


def rh_to_abs_humidity(temp_c: float, rh_pct: float) -> float:
    """Convert relative humidity to absolute humidity (g/m³).

    Uses Tetens equation for saturation vapor pressure and the ideal
    gas law for water vapor.
    """
    svp = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    e_actual = svp * max(0.0, rh_pct) / 100.0
    tk = temp_c + 273.15
    return e_actual * 1000.0 / (_R_W * tk) if tk > 0 else 0.0


def abs_humidity_to_rh(temp_c: float, abs_hum: float) -> float:
    """Convert absolute humidity (g/m³) to relative humidity (%).

    Returns %RH clamped to [0, 100].
    """
    svp = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    if svp <= 0:
        return 0.0
    tk = temp_c + 273.15
    e_actual = abs_hum * _R_W * tk / 1000.0
    return max(0.0, min(100.0, 100.0 * e_actual / svp))


# ---------------------------------------------------------------------------
# Thermodynamic constants (first-principles volume scaling)
# ---------------------------------------------------------------------------

_RHO_CP = 1.2 * 1005.0       # ρ_air × c_p  (J/(m³·K))
_U_WALL = 0.804              # overall heat transfer coeff (W/(m²·K))
_L_V    = 2450.0             # latent heat of vaporisation (J/g)

# Thermal efficiency constants — dimensionless, per actuator type
_ETA_LIGHT   = 0.0181        # fraction of light electrical energy → air heating
_ETA_FAN     = 0.1287        # convective cooling efficiency
_ETA_HUMID   = 0.0386        # evaporative cooling efficiency
_ETA_DEHUM   = 0.00579       # waste heat fraction
_ETA_TRANSP  = 0.00709       # effective latent heat transfer efficiency

# CO2 flow constants
_K_CO2_INJECT = 57.6         # ppm·m³/(W·min) — injection flow rate per watt
_K_RESPIRATION = 0.333       # ppm·m³/(m²_canopy·min) — dark respiration rate

# HVAC (mini-split AC) constants
_COP_HVAC = 3.5              # coefficient of performance (cooling W / electrical W)
_HVAC_DEHUM_GPM_PER_KW = 1.5 # g H₂O removed per min per kW cooling (condensation)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

@dataclass
class PhysicsEngine:
    """Simulates an enclosed growing environment with interdependent physical variables.

    All rates are expressed per minute and scaled by dt_seconds/60 each step.
    Actuators use continuous intensity (0.0–1.0); effects scale linearly.
    """

    # ── State Variables ───────────────────────────────────────────────
    temperature: float = 24.0
    humidity: float = 55.0
    soil_moisture: list[float] = field(
        default_factory=lambda: [70.0, 68.0, 72.0, 65.0]
    )
    light_intensity: float = 0.0   # PPFD µmol/m²/s

    # ── New State Variables ───────────────────────────────────────────
    co2: float = 420.0             # ppm
    leaf_temperature: float = 24.0  # °C

    # ── Computed Values (updated each step) ───────────────────────────
    photosynthesis_rate: float = 0.0   # µmol CO2/m²/s (per unit leaf area)
    transpiration_rate: float = 0.0    # g H2O/min (canopy total)
    vpd: float = 0.0                   # kPa

    # ── Actuator Intensities (0.0–1.0) ────────────────────────────────
    fan: float = 0.0
    exhaust_fan: float = 0.0
    humidifier: float = 0.0
    dehumidifier: float = 0.0
    irrigation: float = 0.0
    light: float = 0.0
    co2_injector: float = 0.0
    hvac: float = 0.0           # optional — only active when spec is in actuator_specs

    # ── Configuration ─────────────────────────────────────────────────
    tent: SpaceConfig = field(default_factory=SpaceConfig)
    plant: PlantModel = field(default_factory=PlantModel)

    ambient_temp: float = 30.0
    ambient_schedule: Callable[[float], float] | None = None
    noise: bool = True

    # Optional per-instance actuator specs (None → use module-level defaults)
    actuator_specs: dict[str, ActuatorSpec] | None = None

    # Optional duct-physics ventilation function (None → linear ACH model)
    ventilation_fn: Callable[[float], float] | None = None

    # Substrate physics: closure + config for realistic soil moisture model
    substrate_fn: Callable | None = None
    substrate_config: SubstrateConfig | None = None
    substrate_container: ContainerGeometry | None = None

    def __post_init__(self) -> None:
        specs = self.actuator_specs if self.actuator_specs is not None else ACTUATOR_SPECS
        self._specs = specs
        self._actuator_map: dict[str, str] = {
            s.relay_id: s.name for s in specs.values()
        }
        self._spec_by_name: dict[str, ActuatorSpec] = {
            s.name: s for s in specs.values()
        }
        # MPC rollout sets this to True so SLSQP sees smooth gradients
        # for binary actuators instead of a step function at 0.5.
        self._relax_quantization: bool = False

        # ── Thermodynamic coefficients (first-principles) ──────────
        V = self.tent.volume_m3
        A = self.tent.floor_area_m2
        C = _RHO_CP * V                      # thermal capacity (J/K)
        w2d = 60.0 / C                       # watts → °C/min

        def _watts(name: str) -> float:
            s = self._spec_by_name.get(name)
            return s.max_watts if s else 0.0

        # Temperature ODE coefficients (°C/min at full intensity)
        self._k_drift     = _U_WALL * A * 60.0 / C
        self._light_dpm   = _ETA_LIGHT * _watts("light") * w2d
        self._fan_dpm     = _ETA_FAN * _watts("fan") * w2d
        self._humid_dpm   = _ETA_HUMID * _watts("humidifier") * w2d
        self._dehum_dpm   = _ETA_DEHUM * _watts("dehumidifier") * w2d
        self._transp_dpm  = _ETA_TRANSP * _L_V * w2d / 60.0

        # HVAC (mini-split AC) — only if spec exists in this room
        hvac_watts = _watts("hvac")
        if hvac_watts > 0:
            cooling_w = _COP_HVAC * hvac_watts     # effective cooling watts
            self._hvac_cool_dpm = cooling_w * w2d   # °C/min at full intensity
            self._hvac_dehum_gpm = _HVAC_DEHUM_GPM_PER_KW * cooling_w / 1000.0
        else:
            self._hvac_cool_dpm = 0.0
            self._hvac_dehum_gpm = 0.0

        # CO2 ODE coefficients
        self._co2_inject  = _K_CO2_INJECT * _watts("co2_injector") / V
        self._co2_resp    = _K_RESPIRATION / V   # multiply by canopy_area at runtime

    def step(self, dt_seconds: float = 30.0, current_hour: float | None = None) -> None:
        """Advance simulation by dt_seconds.

        Args:
            dt_seconds: Time step in seconds.
            current_hour: Fractional hour of day (0.0–23.99) for ambient schedule.
        """
        dt = dt_seconds / 60.0  # convert to minutes for rate calculations

        # Resolve ambient temperature for this step
        ambient = self.ambient_temp
        if self.ambient_schedule is not None and current_hour is not None:
            ambient = self.ambient_schedule(current_hour)

        # ── 1. Air Exchange Rate ─────────────────────────────────────
        if self.ventilation_fn is not None:
            ach = self.ventilation_fn(self.exhaust_fan)
        else:
            ach = self.tent.ach_base + self.tent.ach_max * self.exhaust_fan
        exchange_rate = ach / 60.0  # per minute

        # ── 2. Light Output (PPFD) ──────────────────────────────────
        if self.light > 0:
            self.light_intensity = self.tent.max_ppfd * self.light
        else:
            self.light_intensity = 0.0

        # ── 3. Photosynthesis ────────────────────────────────────────
        self.vpd = vpd_from_temp_rh(self.temperature, self.humidity)

        if self.plant.lai > 0 and self.light_intensity > 0:
            # Light response: rectangular hyperbola (saturating)
            alpha = self.plant.alpha
            pmax = self.plant.pmax
            light_resp = (alpha * self.light_intensity) / (
                1.0 + alpha * self.light_intensity / pmax
            ) if pmax > 0 else 0.0

            # CO2 response: Michaelis-Menten
            co2_resp = self.co2 / (self.co2 + self.plant.km_co2)

            # Temperature response: Gaussian bell curve
            temp_resp = math.exp(
                -((self.temperature - self.plant.t_opt) / self.plant.t_sigma) ** 2
            )

            # VPD stress: stomata close at high VPD (linear decline above 1.5 kPa)
            vpd_stress = max(0.0, 1.0 - max(0.0, self.vpd - 1.5) * 0.5)

            self.photosynthesis_rate = light_resp * co2_resp * temp_resp * vpd_stress
        else:
            self.photosynthesis_rate = 0.0

        # ── 4. Transpiration (simplified Ball-Berry) ─────────────────
        if self.plant.lai > 0 and self.photosynthesis_rate > 0:
            canopy_area = self.plant.lai * self.tent.floor_area_m2
            hs = self.humidity / 100.0  # relative humidity fraction at leaf
            cs = max(self.co2, 100.0)   # CO2 at leaf surface (ppm)
            gs = self.plant.g0 + self.plant.m_bb * self.photosynthesis_rate * hs / cs
            p_atm = 101.3  # kPa
            e_mol = gs * self.vpd / p_atm  # mol H2O/m²leaf/s
            self.transpiration_rate = max(0.0, e_mol * canopy_area * 18.0 * 60.0)  # g/min

            # Water stress: reduce transpiration when substrate is dry
            if self.substrate_config is not None:
                from .substrate_physics import compute_water_stress_factor
                avg_vwc = sum(self.soil_moisture) / len(self.soil_moisture)
                stress_factor = compute_water_stress_factor(
                    avg_vwc,
                    self.substrate_config.stress_onset_vwc,
                    self.substrate_config.wilting_point_vwc,
                )
                self.transpiration_rate *= stress_factor
        else:
            self.transpiration_rate = 0.0

        # ── 5. Temperature ODE ───────────────────────────────────────
        d_temp = 0.0
        d_temp += (ambient - self.temperature) * self._k_drift   # wall conduction
        d_temp += exchange_rate * (ambient - self.temperature)    # ventilation
        d_temp -= self._fan_dpm * self.fan                        # circulation fan
        d_temp += self._light_dpm * self.light                    # light waste heat
        d_temp -= self._transp_dpm * self.transpiration_rate      # latent heat cooling
        d_temp -= self._humid_dpm * self.humidifier               # evaporative cooling
        d_temp += self._dehum_dpm * self.dehumidifier             # waste heat
        d_temp -= self._hvac_cool_dpm * self.hvac                # HVAC active cooling
        self.temperature += d_temp * dt

        # ── 6. Humidity ODE (psychrometric, mass-based) ─────────────
        # Work in absolute humidity (g/m³).  Each source/sink contributes
        # grams of water per minute, divided by room volume.  Convert back
        # to %RH at the end using the (updated) temperature.
        vol = self.tent.volume_m3

        ah = rh_to_abs_humidity(self.temperature, self.humidity)          # g/m³
        ah_ambient = rh_to_abs_humidity(ambient, self.tent.h_ambient)     # g/m³

        d_ah = 0.0  # g/(m³·min)

        # Air exchange: fresh air displaces indoor air
        d_ah += exchange_rate * (ah_ambient - ah)

        # Transpiration: already in g/min from Ball-Berry
        if vol > 0:
            d_ah += self.transpiration_rate / vol

        # Humidifier: g/min from actuator spec (or estimate from watts)
        if self.humidifier > 0 and vol > 0:
            spec = self._spec_by_name.get("humidifier")
            if spec and spec.humidify_g_per_min is not None:
                rate = spec.humidify_g_per_min
            elif spec:
                rate = spec.max_watts * (10.0 / 60.0)
            else:
                rate = 5.0
            d_ah += rate * self.humidifier / vol

        # Dehumidifier: g/min from actuator spec (or estimate from watts)
        if self.dehumidifier > 0 and vol > 0:
            spec = self._spec_by_name.get("dehumidifier")
            if spec and spec.dehumidify_g_per_min is not None:
                rate = spec.dehumidify_g_per_min
            elif spec:
                rate = spec.max_watts * (12.5 / 300.0)
            else:
                rate = 12.5
            d_ah -= rate * self.dehumidifier / vol

        # HVAC dehumidification (condensation on evaporator coil)
        if self.hvac > 0 and vol > 0 and self._hvac_dehum_gpm > 0:
            d_ah -= self._hvac_dehum_gpm * self.hvac / vol

        # Substrate evaporation is added after soil moisture step (section 8)
        ah = max(0.0, ah + d_ah * dt)
        self.humidity = abs_humidity_to_rh(self.temperature, ah)

        # ── 7. CO2 ODE ──────────────────────────────────────────────
        d_co2 = 0.0
        # Injection (watts/volume scaled)
        d_co2 += self._co2_inject * self.co2_injector
        # Plant uptake: photosynthesis draws down CO2
        canopy_area = self.plant.lai * self.tent.floor_area_m2
        uptake_umol_s = self.photosynthesis_rate * canopy_area
        ppm_per_umol_min = 60.0 / (self.tent.volume_m3 * 40.9)
        d_co2 -= uptake_umol_s * ppm_per_umol_min
        # Ventilation: air exchange replaces indoor CO2 with ambient
        d_co2 += exchange_rate * (self.tent.co2_ambient - self.co2)
        # Dark respiration scaled by canopy/volume
        if self.light < 0.01:
            d_co2 += self._co2_resp * canopy_area
        self.co2 += d_co2 * dt

        # ── 8. Soil Moisture ODE (per pot — substrate physics) ───────
        root_uptake_per_pot = (
            self.transpiration_rate * 0.001 / len(self.soil_moisture)
        )
        soil_hum_contrib_g = 0.0  # g/min (total from all containers)
        if self.substrate_fn is not None:
            for i in range(len(self.soil_moisture)):
                new_vwc, _hum_c, _stress, _runoff = self.substrate_fn(
                    self.soil_moisture[i],
                    self.temperature,
                    self.vpd,
                    self.irrigation,
                    root_uptake_per_pot,
                    dt,
                )
                self.soil_moisture[i] = new_vwc
            # Compute room-level evaporation in g/min
            if self.substrate_config is not None and self.substrate_container is not None:
                from .substrate_physics import compute_humidity_contribution, compute_evap_modifier
                avg_vwc = sum(self.soil_moisture) / len(self.soil_moisture)
                evap_mod = compute_evap_modifier(
                    self.temperature, self.vpd,
                    self.substrate_container,
                    self.substrate_config.surface_evap_factor,
                )
                soil_hum_contrib_g = compute_humidity_contribution(
                    avg_vwc, self.substrate_config, evap_mod,
                    self.substrate_container,
                    len(self.soil_moisture),
                )
        else:
            # Fallback: simple linear dry-back (for tests that don't set substrate)
            for i in range(len(self.soil_moisture)):
                d_soil = -0.05                                    # base dry-down
                d_soil += 2.0 * self.irrigation                   # irrigation
                temp_excess = self.temperature - 24.0
                if temp_excess > 0:
                    d_soil -= temp_excess * 0.02
                humidity_deficit = 50.0 - self.humidity
                if humidity_deficit > 0:
                    d_soil -= humidity_deficit * 0.01
                d_soil -= root_uptake_per_pot
                self.soil_moisture[i] += d_soil * dt
            avg_soil = sum(self.soil_moisture) / len(self.soil_moisture)
            if avg_soil > 60.0:
                soil_hum_contrib_g = avg_soil * 0.006
            soil_hum_contrib_g += 1.0 * self.irrigation
        # Apply soil humidity contribution via psychrometric conversion
        if soil_hum_contrib_g > 0 and vol > 0:
            ah_post = rh_to_abs_humidity(self.temperature, self.humidity)
            ah_post = max(0.0, ah_post + (soil_hum_contrib_g / vol) * dt)
            self.humidity = abs_humidity_to_rh(self.temperature, ah_post)

        # ── 9. Leaf Temperature ──────────────────────────────────────
        offset = -self.transpiration_rate * 0.003
        offset = max(-5.0, min(0.5, offset))
        self.leaf_temperature = self.temperature + offset

        # ── 10. Sensor Noise ─────────────────────────────────────────
        if self.noise:
            self.temperature += random.gauss(0, 0.15)
            self.humidity += random.gauss(0, 0.3)
            self.co2 += random.gauss(0, 5.0)
            for i in range(len(self.soil_moisture)):
                self.soil_moisture[i] += random.gauss(0, 0.5)
            if self.light > 0:
                self.light_intensity += random.gauss(0, 5.0)
            self.leaf_temperature += random.gauss(0, 0.1)

        # ── 11. Clamp to Valid Ranges ────────────────────────────────
        self.temperature = max(15.0, min(45.0, self.temperature))
        self.humidity = max(10.0, min(99.0, self.humidity))
        self.co2 = max(200.0, min(2500.0, self.co2))
        self.light_intensity = max(0.0, min(self.tent.max_ppfd, self.light_intensity))
        self.leaf_temperature = max(10.0, min(45.0, self.leaf_temperature))
        soil_max = self.substrate_config.saturation_vwc if self.substrate_config else 100.0
        for i in range(len(self.soil_moisture)):
            self.soil_moisture[i] = max(0.0, min(soil_max, self.soil_moisture[i]))

    # ── State snapshot (fast save/restore for MPC rollouts) ─────────

    def save_state(self) -> tuple:
        """Snapshot all mutable state as a lightweight tuple.

        Used by MPC planner to avoid deepcopy per objective evaluation.
        Only captures values that change during step(); config and
        pre-computed coefficients are immutable and shared.
        """
        return (
            self.temperature,
            self.humidity,
            tuple(self.soil_moisture),
            self.light_intensity,
            self.co2,
            self.leaf_temperature,
            self.photosynthesis_rate,
            self.transpiration_rate,
            self.vpd,
            self.fan,
            self.exhaust_fan,
            self.humidifier,
            self.dehumidifier,
            self.irrigation,
            self.light,
            self.co2_injector,
            self.hvac,
        )

    def restore_state(self, state: tuple) -> None:
        """Restore mutable state from a save_state() snapshot."""
        (
            self.temperature, self.humidity, soil,
            self.light_intensity, self.co2, self.leaf_temperature,
            self.photosynthesis_rate, self.transpiration_rate, self.vpd,
            self.fan, self.exhaust_fan, self.humidifier,
            self.dehumidifier, self.irrigation, self.light,
            self.co2_injector, self.hvac,
        ) = state
        self.soil_moisture = list(soil)

    def get_readings(self) -> dict[str, float]:
        """Return current sensor readings as a flat dict."""
        readings = {
            "temp1": round(self.temperature, 2),
            "hum1": round(self.humidity, 2),
            "light1": round(self.light_intensity, 2),
            "co2_1": round(self.co2, 2),
            "leaf_temp1": round(self.leaf_temperature, 2),
            "vpd1": round(self.vpd, 3),
        }
        for i in range(len(self.soil_moisture)):
            readings[f"soil{i + 1}"] = round(self.soil_moisture[i], 2)
        return readings

    def calibrate(self, readings: dict[str, float]) -> None:
        """Sync internal state with live sensor readings."""
        mapping = {
            "temp1": "temperature",
            "hum1": "humidity",
            "light1": "light_intensity",
            "co2_1": "co2",
            "leaf_temp1": "leaf_temperature",
        }
        for sensor_id, attr in mapping.items():
            if sensor_id in readings:
                setattr(self, attr, readings[sensor_id])
        # Soil moisture probes
        for i in range(len(self.soil_moisture)):
            key = f"soil{i + 1}"
            if key in readings:
                self.soil_moisture[i] = readings[key]
        # Recompute derived values
        self._update_derived()

    def _update_derived(self) -> None:
        """Recompute VPD and leaf temperature from current state."""
        self.vpd = vpd_from_temp_rh(self.temperature, self.humidity)
        # Leaf temperature offset depends on transpiration rate which
        # requires a full step — just use a simple offset here.
        if self.leaf_temperature == 0.0:
            self.leaf_temperature = self.temperature

    def get_actuator_states(self) -> dict[str, bool]:
        """Return actuator states as booleans (backward compat).

        Any intensity > 0.0 reports as True.  Dynamically includes all
        actuators from this engine's spec set (e.g. HVAC when present).
        """
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
        """Set an actuator by relay ID or friendly name.

        Accepts bool (True→1.0, False→0.0) or float (0.0–1.0).
        The value is quantized according to the actuator's control_type
        (binary snaps to 0/1, variable passes through clamped).
        """
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
