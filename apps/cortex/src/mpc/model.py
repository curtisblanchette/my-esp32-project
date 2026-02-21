"""7-state grow room thermodynamic model with analytical Jacobians.

State vector: x = [T_a, w_a, C, T_l, theta, T_s, T_w]
Control vector: u = [u_cool, u_heat, u_dehum, u_hum, u_co2, u_fan, u_irr]
Disturbance vector: d = [Q_light, T_out, w_out, N_plants, phase]

All dynamics are expressed as continuous-time ODEs, then discretized via Padé
approximation for the OSQP QP solver. Jacobians are computed analytically
for speed (< 0.5ms per linearization cycle).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np


# ---------------------------------------------------------------------------
# Index enumerations
# ---------------------------------------------------------------------------

class StateIndex(IntEnum):
    """Indices into the 7-element state vector."""
    T_AIR = 0       # Air temperature (°C)
    W_AIR = 1       # Absolute humidity (g/kg)
    CO2 = 2         # CO2 concentration (ppm)
    T_LEAF = 3      # Leaf temperature (°C)
    THETA = 4       # Substrate water content (m³/m³)
    T_SUPPLY = 5    # HVAC supply air temperature (°C)
    T_WALL = 6      # Wall/thermal mass temperature (°C)

NX = 7  # number of states


class InputIndex(IntEnum):
    """Indices into the 7-element control vector."""
    COOL = 0        # Cooling (0-1)
    HEAT = 1        # Heating (0-1)
    DEHUM = 2       # Dehumidification (0-1)
    HUM = 3         # Humidification (0-1)
    CO2_INJ = 4     # CO2 injection (0/1 binary)
    FAN = 5         # Fan speed (0.2-1.0)
    IRR = 6         # Irrigation (0/1 binary)

NU = 7  # number of inputs
ND = 5  # number of disturbances


# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

RHO_AIR = 1.2          # air density (kg/m³)
CP_AIR = 1006.0        # specific heat of air (J/kg·K)
LAMBDA_V = 2450.0      # latent heat of vaporization (J/g)
SIGMA = 5.67e-8        # Stefan-Boltzmann constant (W/m²·K⁴)
P_ATM = 101.3          # atmospheric pressure (kPa)


# ---------------------------------------------------------------------------
# Room and equipment parameters
# ---------------------------------------------------------------------------

@dataclass
class RoomParams:
    """Physical parameters of the grow room."""
    volume_m3: float = 2.88             # room volume
    floor_area_m2: float = 1.44         # floor area
    ua_wall: float = 5.0                # wall thermal conductance (W/K)
    ua_env: float = 2.0                 # envelope conductance to outdoor (W/K)
    ua_wall_ext: float = 1.0            # wall external conductance (W/K)
    c_wall: float = 50000.0             # wall thermal capacity (J/K)

    # HVAC
    m_dot_supply: float = 0.1           # supply air mass flow rate at full fan (kg/s)
    tau_hvac: float = 60.0              # HVAC time constant (s)
    t_supply_cool: float = 12.0         # cooling coil discharge temp (°C)
    t_supply_heat: float = 40.0         # heating coil discharge temp (°C)

    # Dehumidifier / humidifier
    dehum_capacity_g_s: float = 0.2     # g/s at full power
    hum_capacity_g_s: float = 0.08      # g/s at full power
    condensate_coeff: float = 0.05      # g/s per unit cooling × humidity

    # CO2
    co2_inject_rate: float = 50.0       # ppm/min at full injection rate (volume-normalized)
    co2_ambient: float = 420.0          # outdoor CO2 (ppm)

    # Ventilation
    vent_rate_base: float = 0.001       # base air exchange (fraction/s)
    vent_rate_fan: float = 0.01         # additional exchange per unit fan speed

    # Plant / canopy
    lai: float = 3.0                    # leaf area index
    canopy_area_m2: float = 4.32        # total leaf area (lai × floor_area)
    alpha_sw: float = 0.5               # shortwave absorption coefficient
    emissivity: float = 0.95            # leaf longwave emissivity
    h_conv_base: float = 5.0            # base convective coefficient (W/m²·K)
    c_leaf: float = 500.0               # leaf thermal capacity (J/m²·K)

    # Stomatal conductance
    g_s_max: float = 0.4                # max stomatal conductance (mol/m²/s)
    k_par: float = 200.0               # PAR half-saturation (µmol/m²/s)
    vpd_crit: float = 2.0              # VPD at 50% stomatal closure (kPa)
    c_half: float = 800.0              # CO2 half-saturation for stomatal response (ppm)

    # Photosynthesis
    p_max: float = 30.0                # max net photosynthesis (µmol CO2/m²/s)
    k_co2_photo: float = 300.0         # CO2 Michaelis constant for photosynthesis (ppm)
    r_dark: float = 2.0                # dark respiration (µmol CO2/m²/s)

    # Substrate
    v_sub: float = 0.04                # total substrate volume (m³)
    q_irr: float = 0.001               # irrigation flow rate (m³/s when valve open)
    k_drain: float = 0.001             # drainage coefficient (1/s)
    theta_fc: float = 0.45             # field capacity (m³/m³)
    theta_wilt: float = 0.10           # permanent wilt point (m³/m³)
    rho_water: float = 1000.0          # water density (kg/m³)

    # Light
    par_efficiency: float = 0.40        # fraction of light power → PAR (rest → heat)

    def __post_init__(self):
        self.canopy_area_m2 = self.lai * self.floor_area_m2


# ---------------------------------------------------------------------------
# Derived quantity helpers
# ---------------------------------------------------------------------------

def svp(t: float) -> float:
    """Saturation vapor pressure (kPa) via Tetens equation."""
    return 0.6108 * math.exp(17.27 * t / (t + 237.3))


def dsvp_dt(t: float) -> float:
    """Derivative of SVP with respect to temperature (kPa/°C)."""
    s = svp(t)
    return s * 17.27 * 237.3 / (t + 237.3) ** 2


def w_sat(t: float) -> float:
    """Saturation absolute humidity (g/kg) at temperature t."""
    es = svp(t)
    return 622.0 * es / (P_ATM - es)


def vpd_from_state(t_leaf: float, w_air: float) -> float:
    """VPD (kPa) from leaf temperature and absolute humidity."""
    es_leaf = svp(t_leaf)
    e_actual = w_air / (622.0 + w_air) * P_ATM
    return max(0.0, es_leaf - e_actual)


def rh_from_state(t_air: float, w_air: float) -> float:
    """Relative humidity (%) from air temperature and absolute humidity."""
    ws = w_sat(t_air)
    if ws <= 0:
        return 0.0
    return max(0.0, min(100.0, w_air / ws * 100.0))


# ---------------------------------------------------------------------------
# Stomatal conductance sub-functions
# ---------------------------------------------------------------------------

def f_light(par: float, k_par: float) -> float:
    """Light response factor for stomatal conductance."""
    return par / (par + k_par) if (par + k_par) > 0 else 0.0


def f_vpd_stomata(vpd_val: float, vpd_crit: float) -> float:
    """VPD response factor (stomata close at high VPD)."""
    return 1.0 / (1.0 + vpd_val / vpd_crit) if vpd_crit > 0 else 1.0


def f_water(theta: float, theta_fc: float, theta_wilt: float) -> float:
    """Water stress factor (linear)."""
    if theta_fc <= theta_wilt:
        return 1.0
    return max(0.0, min(1.0, (theta - theta_wilt) / (theta_fc - theta_wilt)))


def f_co2_stomata(co2: float, c_half: float) -> float:
    """CO2 response factor for stomata."""
    return 1.0 / (1.0 + co2 / c_half) if c_half > 0 else 1.0


def stomatal_conductance(
    par: float, vpd_val: float, theta: float, co2: float, p: RoomParams,
) -> float:
    """Full stomatal conductance (mol/m²/s)."""
    return (
        p.g_s_max
        * f_light(par, p.k_par)
        * f_vpd_stomata(vpd_val, p.vpd_crit)
        * f_water(theta, p.theta_fc, p.theta_wilt)
        * f_co2_stomata(co2, p.c_half)
    )


def transpiration_rate(gs: float, vpd_val: float, canopy_area: float) -> float:
    """Transpiration rate (g/s) from stomatal conductance and VPD."""
    # mol H2O/m²/s → g/s (18 g/mol)
    e_mol = gs * vpd_val / P_ATM
    return max(0.0, e_mol * canopy_area * 18.0)


def net_photosynthesis(par: float, co2: float, p: RoomParams) -> float:
    """Net photosynthesis rate (µmol CO2/m²/s) via Michaelis-Menten."""
    if par <= 0:
        return -p.r_dark
    light_resp = p.p_max * par / (par + p.k_par)
    co2_resp = co2 / (co2 + p.k_co2_photo) if (co2 + p.k_co2_photo) > 0 else 0.0
    return light_resp * co2_resp - p.r_dark


# ---------------------------------------------------------------------------
# Grow room model
# ---------------------------------------------------------------------------

class GrowRoomModel:
    """7-state continuous-time grow room model.

    Provides:
    - dynamics(x, u, d) → dx/dt
    - linearize(x, u, d) → A_d, B_d, Bd_d  (discrete-time matrices)
    - derived_outputs(x) → VPD, RH, dryback_rate, transpiration
    """

    def __init__(self, params: RoomParams | None = None):
        self.p = params or RoomParams()

    def dynamics(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
    ) -> np.ndarray:
        """Compute continuous-time state derivatives dx/dt.

        Args:
            x: State vector [T_a, w_a, C, T_l, theta, T_s, T_w]
            u: Control vector [u_cool, u_heat, u_dehum, u_hum, u_co2, u_fan, u_irr]
            d: Disturbance vector [Q_light, T_out, w_out, N_plants, phase]

        Returns:
            dxdt: Time derivatives of state vector (7,)
        """
        p = self.p
        T_a = x[StateIndex.T_AIR]
        w_a = x[StateIndex.W_AIR]
        C = x[StateIndex.CO2]
        T_l = x[StateIndex.T_LEAF]
        theta = x[StateIndex.THETA]
        T_s = x[StateIndex.T_SUPPLY]
        T_w = x[StateIndex.T_WALL]

        u_cool = u[InputIndex.COOL]
        u_heat = u[InputIndex.HEAT]
        u_dehum = u[InputIndex.DEHUM]
        u_hum = u[InputIndex.HUM]
        u_co2 = u[InputIndex.CO2_INJ]
        u_fan = u[InputIndex.FAN]
        u_irr = u[InputIndex.IRR]

        Q_light = d[0]
        T_out = d[1]
        w_out = d[2]

        # Precompute common terms
        thermal_cap = RHO_AIR * p.volume_m3 * CP_AIR  # J/K
        vpd_val = vpd_from_state(T_l, w_a)
        par = Q_light * p.par_efficiency / (p.floor_area_m2 + 1e-9)  # approximate PAR from light watts
        gs = stomatal_conductance(par, vpd_val, theta, C, p)
        E_t = transpiration_rate(gs, vpd_val, p.canopy_area_m2)
        h_c = p.h_conv_base * max(0.2, u_fan) ** 0.5  # convective coefficient

        # Ventilation rate
        m_vent = p.vent_rate_base + p.vent_rate_fan * u_fan

        # HVAC supply temperature target
        t_s_target = T_a  # default: no heating/cooling
        if u_cool > u_heat:
            t_s_target = p.t_supply_cool
        elif u_heat > u_cool:
            t_s_target = p.t_supply_heat

        # Q_hvac: supply air sensible heat
        fan_flow = u_fan * p.m_dot_supply
        Q_hvac = fan_flow * CP_AIR * (T_s - T_a)

        # Light heat load (non-PAR fraction)
        Q_light_heat = Q_light * (1.0 - p.par_efficiency)

        dxdt = np.zeros(NX)

        # 1. Air temperature
        dxdt[StateIndex.T_AIR] = (
            Q_light_heat
            + Q_hvac
            - LAMBDA_V * E_t
            + p.ua_wall * (T_w - T_a)
            + p.ua_env * (T_out - T_a)
        ) / thermal_cap

        # 2. Absolute humidity (g/kg mixing ratio)
        mass_air = RHO_AIR * p.volume_m3  # kg of dry air in room
        condensate = p.condensate_coeff * u_cool * max(0, w_a - w_sat(T_s))

        # Mass-based sources (g/s) → convert to g/kg/s by dividing by air mass
        moisture_mass_g_s = (
            E_t
            - p.dehum_capacity_g_s * u_dehum
            + p.hum_capacity_g_s * u_hum
            - condensate
        )
        # Ventilation: air exchange (already in g/kg/s — concentration difference)
        vent_g_kg_s = m_vent * (w_out - w_a)

        dxdt[StateIndex.W_AIR] = moisture_mass_g_s / mass_air + vent_g_kg_s

        # 3. CO2 concentration
        P_net = net_photosynthesis(par, C, p)
        # µmol/m²/s → ppm/s: (µmol/s) / (volume_m3 * 1e-6 mol/µmol * 1e6 ppm correction)
        # Simplified: ppm/s = P_net * canopy_area / (V * 40.9) * 1e6...
        # Actually: 1 µmol CO2 in V m³ at 1 atm, 25°C ≈ 24.5 µL ≈ 24.5e-6 L
        # ppm = µL/L, so 1 µmol in V m³ = 24.5e-3 / V ppm
        ppm_per_umol_s = 0.0245 / max(p.volume_m3, 0.1)
        dxdt[StateIndex.CO2] = (
            -P_net * p.canopy_area_m2 * ppm_per_umol_s
            + p.co2_inject_rate / 60.0 * u_co2
            + m_vent * (p.co2_ambient - C)
        )

        # 4. Leaf temperature
        R_sw = Q_light / max(p.canopy_area_m2, 0.01)  # W/m²
        # Linearized radiation: 4·ε·σ·T³·(T_l - T_a) where T in Kelvin
        T_l_K = T_l + 273.15
        T_a_K = T_a + 273.15
        rad_lin = p.emissivity * SIGMA * 4.0 * ((T_l_K + T_a_K) / 2.0) ** 3 * (T_l - T_a)
        evap_cool = LAMBDA_V * E_t / max(p.canopy_area_m2, 0.01)
        dxdt[StateIndex.T_LEAF] = (
            p.alpha_sw * R_sw
            - rad_lin
            - h_c * (T_l - T_a)
            - evap_cool
        ) / p.c_leaf

        # 5. Substrate water content (VWC)
        dxdt[StateIndex.THETA] = (
            p.q_irr * u_irr
            - E_t / 1000.0 * p.canopy_area_m2 / (p.rho_water * p.v_sub)
            - p.k_drain * max(0.0, theta - p.theta_fc)
        ) / max(p.v_sub, 0.001)

        # 6. Supply air temperature (first-order lag)
        dxdt[StateIndex.T_SUPPLY] = (t_s_target - T_s) / max(p.tau_hvac, 1.0)

        # 7. Wall/thermal mass temperature
        dxdt[StateIndex.T_WALL] = (
            p.ua_wall * (T_a - T_w)
            + p.ua_wall_ext * (T_out - T_w)
        ) / max(p.c_wall, 1.0)

        return dxdt

    def linearize(
        self,
        x0: np.ndarray,
        u0: np.ndarray,
        d0: np.ndarray,
        dt: float = 60.0,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Compute discrete-time A_d, B_d, Bd_d matrices via numerical Jacobians.

        Uses central finite differences for robustness. On a modern CPU this
        takes < 2ms for the 7-state system (7 states × 7 inputs × 2 evals each).

        For the OSQP solver the matrices only need to be recomputed once per
        MPC cycle (1 Hz), so even 2ms is well within budget.

        Args:
            x0: Linearization point state (7,)
            u0: Linearization point input (7,)
            d0: Linearization point disturbance (5,)
            dt: Discretization time step (seconds)

        Returns:
            A_d: Discrete state transition matrix (7×7)
            B_d: Discrete input matrix (7×7)
            Bd_d: Discrete disturbance matrix (7×5)
        """
        eps_x = np.array([0.01, 0.01, 1.0, 0.01, 0.001, 0.01, 0.01])
        eps_u = np.array([0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001])
        eps_d = np.array([1.0, 0.01, 0.01, 0.1, 0.1])

        # A_c: ∂f/∂x
        A_c = np.zeros((NX, NX))
        for j in range(NX):
            x_plus = x0.copy()
            x_minus = x0.copy()
            x_plus[j] += eps_x[j]
            x_minus[j] -= eps_x[j]
            f_plus = self.dynamics(x_plus, u0, d0)
            f_minus = self.dynamics(x_minus, u0, d0)
            A_c[:, j] = (f_plus - f_minus) / (2.0 * eps_x[j])

        # B_c: ∂f/∂u
        B_c = np.zeros((NX, NU))
        for j in range(NU):
            u_plus = u0.copy()
            u_minus = u0.copy()
            u_plus[j] = min(1.0, u0[j] + eps_u[j])
            u_minus[j] = max(0.0, u0[j] - eps_u[j])
            actual_eps = u_plus[j] - u_minus[j]
            if actual_eps < 1e-10:
                continue
            f_plus = self.dynamics(x0, u_plus, d0)
            f_minus = self.dynamics(x0, u_minus, d0)
            B_c[:, j] = (f_plus - f_minus) / actual_eps

        # Bd_c: ∂f/∂d
        Bd_c = np.zeros((NX, ND))
        for j in range(ND):
            d_plus = d0.copy()
            d_minus = d0.copy()
            d_plus[j] += eps_d[j]
            d_minus[j] -= eps_d[j]
            f_plus = self.dynamics(x0, u0, d_plus)
            f_minus = self.dynamics(x0, u0, d_minus)
            Bd_c[:, j] = (f_plus - f_minus) / (2.0 * eps_d[j])

        # Discretize via Padé(2) approximation
        # A_d ≈ I + A_c·dt + 0.5·(A_c·dt)²
        I = np.eye(NX)
        A_c_dt = A_c * dt
        A_d = I + A_c_dt + 0.5 * A_c_dt @ A_c_dt

        # B_d ≈ (I + 0.5·A_c·dt) · B_c · dt
        B_d = (I + 0.5 * A_c_dt) @ B_c * dt

        # Bd_d ≈ (I + 0.5·A_c·dt) · Bd_c · dt
        Bd_d = (I + 0.5 * A_c_dt) @ Bd_c * dt

        return A_d, B_d, Bd_d

    def derived_outputs(self, x: np.ndarray) -> dict[str, float]:
        """Compute derived quantities from state vector."""
        T_a = x[StateIndex.T_AIR]
        w_a = x[StateIndex.W_AIR]
        T_l = x[StateIndex.T_LEAF]
        theta = x[StateIndex.THETA]

        vpd_val = vpd_from_state(T_l, w_a)
        rh_val = rh_from_state(T_a, w_a)

        return {
            "VPD": vpd_val,
            "RH": rh_val,
            "T_air": T_a,
            "CO2": x[StateIndex.CO2],
            "T_leaf": T_l,
            "theta": theta,
            "T_supply": x[StateIndex.T_SUPPLY],
            "T_wall": x[StateIndex.T_WALL],
        }

    def default_state(self) -> np.ndarray:
        """Return a reasonable default initial state."""
        return np.array([
            25.0,   # T_air (°C)
            10.0,   # w_air (g/kg) — ~55% RH at 25°C
            420.0,  # CO2 (ppm)
            24.0,   # T_leaf (°C)
            0.35,   # theta (m³/m³)
            20.0,   # T_supply (°C)
            23.0,   # T_wall (°C)
        ])

    def default_input(self) -> np.ndarray:
        """Return a reasonable default control input."""
        return np.array([
            0.0,    # u_cool
            0.0,    # u_heat
            0.0,    # u_dehum
            0.0,    # u_hum
            0.0,    # u_co2
            0.3,    # u_fan (minimum 20%, default 30%)
            0.0,    # u_irr
        ])

    def default_disturbance(self) -> np.ndarray:
        """Return a default disturbance vector (lights on, mild outdoor)."""
        return np.array([
            300.0,  # Q_light (W)
            25.0,   # T_out (°C)
            8.0,    # w_out (g/kg)
            4.0,    # N_plants
            2.0,    # phase (veg=2)
        ])
