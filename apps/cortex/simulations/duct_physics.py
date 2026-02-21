"""Duct physics model for realistic airflow simulation.

Computes air exchange rates from first principles using fan performance
curves, duct geometry / Darcy-Weisbach system resistance, and fan affinity
laws.  All public functions are pure — no classes, no mutable state.

YAML inputs are in imperial units (CFM, inWC, inches, feet) — matching HVAC
equipment datasheets.  Internal math uses SI (m³/s, Pa, metres).

References:
- ASHRAE Fundamentals: Duct Design, Fan Curves
- Fan Affinity Laws (Speed, Pressure, Power)
- Darcy-Weisbach equation (simplified for circular ducts)
"""

from __future__ import annotations

import math
from typing import Callable

# ---------------------------------------------------------------------------
# Unit conversions
# ---------------------------------------------------------------------------

_CFM_TO_M3S = 0.000471947          # 1 CFM = 0.000471947 m³/s
_INWC_TO_PA = 249.089              # 1 inch water column = 249.089 Pa
_INCH_TO_M = 0.0254                # 1 inch = 0.0254 m
_FT_TO_M = 0.3048                  # 1 foot = 0.3048 m

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

_AIR_DENSITY = 1.2                 # kg/m³ (sea level, ~20°C)

# Darcy friction factors (dimensionless)
FRICTION_SMOOTH = 0.02             # rigid galvanised duct
FRICTION_FLEX = 0.04               # flexible duct (~2× rigid)

_FRICTION_BY_MATERIAL = {
    "smooth": FRICTION_SMOOTH,
    "flex": FRICTION_FLEX,
}

# Each 90° elbow adds ~12 diameters of equivalent straight length
_ELBOW_EQ_DIAMETERS = 12.0

# Carbon filter reference: ~150 Pa drop at 0.1 m³/s (~212 CFM)
_CARBON_FILTER_PA = 150.0          # Pa at reference flow
_CARBON_FILTER_Q_REF = 0.1         # m³/s reference flow


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def compute_system_resistance(
    diameter_m: float,
    length_m: float,
    friction_factor: float,
    num_elbows: int = 0,
    has_carbon_filter: bool = False,
) -> float:
    """Compute total system resistance coefficient K (Pa·s²/m⁶).

    ΔP_sys = K × Q²  where Q is volumetric flow in m³/s.

    K is derived from the Darcy-Weisbach equation for circular ducts,
    plus optional carbon filter resistance.

    Args:
        diameter_m: Duct internal diameter in metres.
        length_m: Total straight duct run in metres.
        friction_factor: Darcy friction factor (0.02 smooth, 0.04 flex).
        num_elbows: Number of 90° elbows (each adds 12 diameters).
        has_carbon_filter: Whether a carbon filter is present.

    Returns:
        System resistance K in Pa·s²/m⁶.
    """
    area = math.pi * (diameter_m / 2.0) ** 2
    eq_length = length_m + num_elbows * _ELBOW_EQ_DIAMETERS * diameter_m

    # Darcy-Weisbach: ΔP = f × (L/D) × (ρ / (2A²)) × Q²
    k_duct = friction_factor * (eq_length / diameter_m) * (_AIR_DENSITY / (2.0 * area ** 2))

    # Carbon filter: modelled as additional quadratic resistance
    # K_filter = ΔP_ref / Q_ref²
    k_filter = 0.0
    if has_carbon_filter:
        k_filter = _CARBON_FILTER_PA / (_CARBON_FILTER_Q_REF ** 2)

    return k_duct + k_filter


def compute_operating_flow_m3s(
    q_max_m3s: float,
    p_max_pa: float,
    system_k: float,
    speed_fraction: float,
) -> float:
    """Find the fan operating point — where fan curve meets system curve.

    Fan curve (quadratic):
        ΔP_fan = P_eff × (1 − (Q / Q_eff)²)
    where:
        Q_eff = Q_max × speed   (fan affinity: flow ~ speed)
        P_eff = P_max × speed²  (fan affinity: pressure ~ speed²)

    System curve:
        ΔP_sys = K × Q²

    Setting equal and solving:
        Q_actual = Q_eff × √(P_eff / (P_eff + K × Q_eff²))

    Args:
        q_max_m3s: Fan free-air delivery (max flow at zero static pressure) in m³/s.
        p_max_pa: Fan max static pressure (at zero flow) in Pa.
        system_k: System resistance coefficient from ``compute_system_resistance()``.
        speed_fraction: Fan speed 0.0–1.0.

    Returns:
        Actual delivered volumetric flow in m³/s.
    """
    if speed_fraction <= 0.0 or q_max_m3s <= 0.0:
        return 0.0

    speed = min(1.0, speed_fraction)

    # Fan affinity laws
    q_eff = q_max_m3s * speed
    p_eff = p_max_pa * speed ** 2

    if system_k <= 0.0:
        # Zero resistance → free-air delivery at this speed
        return q_eff

    # Analytical operating point solve
    denom = p_eff + system_k * q_eff ** 2
    if denom <= 0.0:
        return 0.0

    return q_eff * math.sqrt(p_eff / denom)


def compute_ach(
    flow_m3s: float,
    volume_m3: float,
    passive_ach: float = 0.0,
) -> float:
    """Convert volumetric flow to air changes per hour.

    ACH = passive_ach + (Q × 3600) / V

    Args:
        flow_m3s: Delivered air flow in m³/s.
        volume_m3: Room volume in m³.
        passive_ach: Natural infiltration ACH (building leaks).

    Returns:
        Total air changes per hour.
    """
    if volume_m3 <= 0.0:
        return passive_ach
    return passive_ach + (flow_m3s * 3600.0) / volume_m3


def build_ventilation_fn(
    rated_cfm: float,
    max_static_pressure_inwc: float,
    diameter_in: float,
    length_ft: float,
    material: str = "flex",
    elbows_90: int = 0,
    has_carbon_filter: bool = False,
    passive_ach: float = 0.5,
    volume_m3: float = 1.0,
) -> Callable[[float], float]:
    """Build a closure mapping fan intensity (0.0–1.0) → ACH.

    Pre-computes the system resistance K from duct geometry so the
    per-step evaluation is a simple analytical solve — no iteration.

    Args:
        rated_cfm: Fan free-air delivery from datasheet (CFM).
        max_static_pressure_inwc: Fan max static pressure (inches WC).
        diameter_in: Duct diameter in inches.
        length_ft: Total straight duct length in feet.
        material: Duct material — "smooth" (rigid) or "flex".
        elbows_90: Number of 90° elbows.
        has_carbon_filter: Whether a carbon filter is present in the duct.
        passive_ach: Natural infiltration ACH.
        volume_m3: Room volume in m³ (for ACH calculation).

    Returns:
        Callable[[float], float] mapping fan intensity → ACH.
    """
    # Convert imperial → SI
    q_max = rated_cfm * _CFM_TO_M3S
    p_max = max_static_pressure_inwc * _INWC_TO_PA
    diameter = diameter_in * _INCH_TO_M
    length = length_ft * _FT_TO_M

    friction = _FRICTION_BY_MATERIAL.get(material, FRICTION_FLEX)

    # Pre-compute system resistance
    system_k = compute_system_resistance(
        diameter_m=diameter,
        length_m=length,
        friction_factor=friction,
        num_elbows=elbows_90,
        has_carbon_filter=has_carbon_filter,
    )

    def _ventilation_fn(fan_intensity: float) -> float:
        flow = compute_operating_flow_m3s(q_max, p_max, system_k, fan_intensity)
        return compute_ach(flow, volume_m3, passive_ach)

    return _ventilation_fn
