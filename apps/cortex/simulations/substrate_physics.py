"""Substrate physics model for realistic soil moisture simulation.

Models volumetric water content (VWC) dynamics from first principles using
substrate-specific physical properties, exponential dry-back curves,
infiltration-limited irrigation, gravity drainage, and container geometry.

Three cannabis-industry presets: **rockwool**, **coco/perlite 70:30**, and
**living soil**.  All public functions are pure — no classes with mutable
state, same pattern as ``duct_physics.py``.

Substrate properties are calibrated to match real-world dry-back timescales
measured by Aroya / TEROS substrate sensors in commercial cannabis facilities.

References:
- METER Group: Understanding Soil Moisture Sensor Data
- Aroya: Substrate Management for Cannabis
- KiS Organics: Living Soil for Cannabis Production
- Coco for Cannabis: Water Holding Capacity & Dry-Back
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContainerGeometry:
    """Container geometry for surface-area-to-volume ratio calculations.

    Small pots dry faster than beds because more surface area is exposed
    relative to volume, increasing evaporation.
    """
    volume_liters: float
    surface_area_m2: float

    @property
    def sa_to_vol_ratio(self) -> float:
        """Surface area to volume ratio (m²/L) — higher = dries faster."""
        if self.volume_liters <= 0:
            return 0.0
        return self.surface_area_m2 / self.volume_liters


@dataclass(frozen=True)
class SubstrateConfig:
    """Physical properties of a growing substrate.

    All moisture values are volumetric water content (VWC) as a percentage
    (0–100).  Real-world sensors (TEROS, Aroya) report in this range.

    Attributes:
        name: Human-readable substrate name.
        saturation_vwc: Maximum VWC when all pores are filled (above → runoff).
        field_capacity_vwc: VWC after free drainage stops (gravity equilibrium).
        stress_onset_vwc: Below this, plant water stress begins (transpiration reduced).
        wilting_point_vwc: Below this, plants cannot extract water (transpiration = 0).
        k_dry: Base dry-back rate constant (1/min).  Calibrated so that
            FC → stress_onset takes approximately the expected real-world hours.
        infiltration_rate: Max water absorption rate (%VWC/min at full intensity).
        drainage_rate: Gravity drainage rate above field capacity (%VWC/min).
        surface_evap_factor: Relative surface evaporation (1.0 = fully exposed,
            0.3 = covered/wrapped like rockwool slabs).
    """
    name: str
    saturation_vwc: float
    field_capacity_vwc: float
    stress_onset_vwc: float
    wilting_point_vwc: float
    k_dry: float
    infiltration_rate: float
    drainage_rate: float
    surface_evap_factor: float


# ---------------------------------------------------------------------------
# Container geometry helpers
# ---------------------------------------------------------------------------

def pot_geometry(diameter_cm: float, height_cm: float) -> ContainerGeometry:
    """Compute geometry for a cylindrical pot."""
    r_m = diameter_cm / 200.0  # cm → m radius
    h_m = height_cm / 100.0
    volume_l = math.pi * (diameter_cm / 2.0) ** 2 * height_cm / 1000.0  # cm³ → L
    # Surface area: top circle + side wall (bottom is sealed)
    sa = math.pi * r_m ** 2 + 2 * math.pi * r_m * h_m
    return ContainerGeometry(volume_liters=volume_l, surface_area_m2=sa)


def bed_geometry(length_cm: float, width_cm: float, depth_cm: float) -> ContainerGeometry:
    """Compute geometry for a rectangular raised bed / slab."""
    volume_l = length_cm * width_cm * depth_cm / 1000.0  # cm³ → L
    l_m = length_cm / 100.0
    w_m = width_cm / 100.0
    d_m = depth_cm / 100.0
    # Surface area: top face only (sides usually enclosed, bottom sealed)
    sa = l_m * w_m
    return ContainerGeometry(volume_liters=volume_l, surface_area_m2=sa)


def default_container() -> ContainerGeometry:
    """Default container: standard 5-gallon nursery pot."""
    return pot_geometry(diameter_cm=30.0, height_cm=30.0)


# ---------------------------------------------------------------------------
# Substrate presets
# ---------------------------------------------------------------------------

def rockwool() -> SubstrateConfig:
    """Grodan-style rockwool slab — fast-draining, precise control.

    FC→stress: ~4h.  Used in precision commercial facilities.
    Slabs are typically wrapped (low surface evaporation).
    """
    return SubstrateConfig(
        name="rockwool",
        saturation_vwc=85.0,
        field_capacity_vwc=65.0,
        stress_onset_vwc=25.0,
        wilting_point_vwc=15.0,
        k_dry=0.012,
        infiltration_rate=8.0,
        drainage_rate=0.5,
        surface_evap_factor=0.3,
    )


def coco_perlite_70_30() -> SubstrateConfig:
    """70% coco coir / 30% perlite — moderate retention, good drainage.

    FC→stress: ~6h.  Most common substrate in commercial cannabis.
    """
    return SubstrateConfig(
        name="coco_perlite_70_30",
        saturation_vwc=72.0,
        field_capacity_vwc=48.0,
        stress_onset_vwc=22.0,
        wilting_point_vwc=14.0,
        k_dry=0.008,
        infiltration_rate=5.0,
        drainage_rate=0.3,
        surface_evap_factor=0.7,
    )


def living_soil() -> SubstrateConfig:
    """Living soil — perlite/soil/castings mix, high retention, slow drain.

    FC→stress: ~12-18h.  Used in organic / no-till cannabis operations.
    Top surface exposed (mulch optional, modelled as moderate exposure).
    """
    return SubstrateConfig(
        name="living_soil",
        saturation_vwc=58.0,
        field_capacity_vwc=42.0,
        stress_onset_vwc=20.0,
        wilting_point_vwc=12.0,
        k_dry=0.004,
        infiltration_rate=2.5,
        drainage_rate=0.15,
        surface_evap_factor=0.8,
    )


SUBSTRATE_PRESETS: dict[str, Callable[[], SubstrateConfig]] = {
    "rockwool": rockwool,
    "coco_perlite_70_30": coco_perlite_70_30,
    "living_soil": living_soil,
}


# ---------------------------------------------------------------------------
# Reference conditions for evaporation modifier
# ---------------------------------------------------------------------------

_REF_TEMP_C = 25.0       # reference temperature for Q10
_REF_VPD_KPA = 1.0       # reference VPD (25°C, 50% RH ≈ 1.0 kPa)
_REF_SA_VOL = 0.015      # reference SA:V ratio (~5gal pot)
_Q10 = 2.0               # evaporation doubles per 10°C rise


# ---------------------------------------------------------------------------
# Core pure functions
# ---------------------------------------------------------------------------

def compute_evap_modifier(
    temperature: float,
    vpd_kpa: float,
    container: ContainerGeometry,
    surface_evap_factor: float,
) -> float:
    """Compute evaporation rate modifier relative to reference conditions.

    Combines:
    - Temperature Q10 effect (doubles per 10°C rise)
    - VPD effect (higher VPD → faster evaporation, normalized to reference)
    - Container geometry (higher SA:V → faster evaporation)
    - Surface exposure (wrapped slabs evaporate less than open beds)

    Returns a dimensionless multiplier (1.0 = reference conditions).
    """
    # Temperature: Q10 model
    temp_factor = _Q10 ** ((temperature - _REF_TEMP_C) / 10.0)

    # VPD: linear scaling, clamped to prevent negative
    vpd_factor = max(0.1, vpd_kpa / _REF_VPD_KPA) if _REF_VPD_KPA > 0 else 1.0

    # Container geometry: SA:V ratio relative to reference
    sa_vol = container.sa_to_vol_ratio
    geom_factor = max(0.5, min(2.0, sa_vol / _REF_SA_VOL)) if _REF_SA_VOL > 0 else 1.0

    return temp_factor * vpd_factor * geom_factor * surface_evap_factor


def compute_water_stress_factor(
    vwc: float,
    stress_onset_vwc: float,
    wilting_point_vwc: float,
) -> float:
    """Compute plant water stress factor (0.0–1.0) that scales transpiration.

    - Above stress_onset: full transpiration (1.0)
    - Between wilting_point and stress_onset: linear reduction
    - At or below wilting_point: no transpiration (0.0)
    """
    if vwc >= stress_onset_vwc:
        return 1.0
    if vwc <= wilting_point_vwc:
        return 0.0
    span = stress_onset_vwc - wilting_point_vwc
    if span <= 0:
        return 0.0
    return (vwc - wilting_point_vwc) / span


def compute_dryback(
    vwc: float,
    substrate: SubstrateConfig,
    evap_modifier: float,
    root_uptake_rate: float,
    dt_minutes: float,
) -> float:
    """Compute VWC change from dry-back (evaporation + root uptake + drainage).

    Exponential dry-back model:
        d_vwc_evap = -k_dry * evap_modifier * (vwc - wilting_point)

    Above field capacity, gravity drainage adds an additional loss:
        d_vwc_drain = -drainage_rate * (vwc - field_capacity) / (saturation - field_capacity)

    Root uptake is a separate linear draw proportional to transpiration.

    Returns the total VWC delta for this timestep (always <= 0 when not irrigating).
    """
    d_vwc = 0.0

    # Evaporation: exponential decay toward wilting point
    excess = max(0.0, vwc - substrate.wilting_point_vwc)
    d_vwc -= substrate.k_dry * evap_modifier * excess

    # Gravity drainage above field capacity
    if vwc > substrate.field_capacity_vwc:
        fc_excess = vwc - substrate.field_capacity_vwc
        sat_range = substrate.saturation_vwc - substrate.field_capacity_vwc
        if sat_range > 0:
            d_vwc -= substrate.drainage_rate * (fc_excess / sat_range)

    # Root uptake from transpiration (distributed across pots)
    d_vwc -= root_uptake_rate

    return d_vwc * dt_minutes


def compute_irrigation(
    vwc: float,
    substrate: SubstrateConfig,
    irrigation_intensity: float,
    dt_minutes: float,
) -> tuple[float, float]:
    """Compute VWC change from irrigation.

    Water input is limited by the substrate's infiltration rate.
    Above saturation → runoff (excess water that can't be absorbed).

    Args:
        vwc: Current volumetric water content (%).
        substrate: Substrate configuration.
        irrigation_intensity: Irrigation actuator intensity (0.0–1.0).
        dt_minutes: Timestep in minutes.

    Returns:
        (vwc_delta, runoff): VWC increase and any runoff fraction.
    """
    if irrigation_intensity <= 0.0:
        return 0.0, 0.0

    # Water input rate limited by infiltration capacity
    water_in = substrate.infiltration_rate * irrigation_intensity * dt_minutes

    # How much can the substrate absorb?
    headroom = max(0.0, substrate.saturation_vwc - vwc)
    absorbed = min(water_in, headroom)
    runoff = max(0.0, water_in - absorbed)

    return absorbed, runoff


def compute_humidity_contribution(
    vwc: float,
    substrate: SubstrateConfig,
    evap_modifier: float,
    container: ContainerGeometry,
    num_containers: int,
) -> float:
    """Compute substrate surface evaporation in grams of water per minute.

    Returns total evaporation rate from all containers (g/min).
    The caller (physics engine) divides by room volume and converts to %RH
    via psychrometric equations.

    The evap_modifier already includes temperature Q10, VPD scaling,
    container SA:V ratio, and surface exposure effects.
    """
    if num_containers <= 0:
        return 0.0

    # Wetness fraction: 0 at wilting point, 1 at field capacity
    fc_range = substrate.field_capacity_vwc - substrate.wilting_point_vwc
    if fc_range <= 0:
        return 0.0
    wetness = max(0.0, min(1.0, (vwc - substrate.wilting_point_vwc) / fc_range))

    # Total surface area of all containers
    total_sa = container.surface_area_m2 * num_containers

    # Surface evaporation coefficient: g H₂O per m² of substrate surface per
    # minute at reference conditions (evap_mod=1.0, surface_evap_factor=1.0).
    # Calibrated against Penman-Monteith style substrate evaporation rates.
    K_EVAP_G = 0.15  # g/(m²·min)

    return K_EVAP_G * wetness * evap_modifier * substrate.surface_evap_factor * total_sa


# ---------------------------------------------------------------------------
# High-level closure builder
# ---------------------------------------------------------------------------

def build_substrate_fn(
    substrate: SubstrateConfig,
    container: ContainerGeometry,
) -> Callable:
    """Build a closure capturing substrate + container config.

    Returns a callable with signature:
        fn(vwc, temperature, vpd_kpa, irrigation_intensity,
           root_uptake_rate, dt_minutes)
        → (new_vwc, humidity_contribution, water_stress_factor, runoff)

    Pre-captures the substrate and container so per-step evaluation is fast.
    """

    def _substrate_step(
        vwc: float,
        temperature: float,
        vpd_kpa: float,
        irrigation_intensity: float,
        root_uptake_rate: float,
        dt_minutes: float,
    ) -> tuple[float, float, float, float]:
        """Single-step substrate physics evaluation.

        Returns:
            (new_vwc, humidity_contribution, water_stress_factor, runoff)
        """
        # Evaporation modifier for current conditions
        evap_mod = compute_evap_modifier(
            temperature, vpd_kpa, container, substrate.surface_evap_factor,
        )

        # Dry-back (evaporation + root uptake + drainage)
        d_vwc_dry = compute_dryback(
            vwc, substrate, evap_mod, root_uptake_rate, dt_minutes,
        )

        # Irrigation
        d_vwc_irr, runoff = compute_irrigation(
            vwc, substrate, irrigation_intensity, dt_minutes,
        )

        # New VWC, clamped to [0, saturation]
        new_vwc = max(0.0, min(substrate.saturation_vwc, vwc + d_vwc_dry + d_vwc_irr))

        # Humidity contribution from surface evaporation
        # Use the average VWC for this step
        avg_vwc = (vwc + new_vwc) / 2.0
        hum_contrib = compute_humidity_contribution(
            avg_vwc, substrate, evap_mod, container, 1,
        )

        # Water stress factor for transpiration scaling
        stress = compute_water_stress_factor(
            new_vwc, substrate.stress_onset_vwc, substrate.wilting_point_vwc,
        )

        return new_vwc, hum_contrib, stress, runoff

    return _substrate_step
