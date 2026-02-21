"""Constraint construction for the MPC QP problem.

Builds hard constraints (actuator limits, rate limits, safety bounds),
soft constraints (comfort bands with slack variables), and mutual exclusion
constraints for the OSQP solver.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ActuatorLimits:
    """Box constraints on control inputs."""
    u_min: np.ndarray = field(default_factory=lambda: np.array([
        0.0,    # u_cool
        0.0,    # u_heat
        0.0,    # u_dehum
        0.0,    # u_hum
        0.0,    # u_co2
        0.2,    # u_fan (minimum 20% for airflow)
        0.0,    # u_irr
    ]))
    u_max: np.ndarray = field(default_factory=lambda: np.array([
        1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
    ]))


@dataclass
class RateLimits:
    """Actuator rate-of-change limits per control step."""
    du_max: np.ndarray = field(default_factory=lambda: np.array([
        0.1,    # u_cool: 10% per step (compressor protection)
        0.2,    # u_heat: 20% per step
        0.15,   # u_dehum: 15% per step
        1.0,    # u_hum: no rate limit (can switch freely)
        1.0,    # u_co2: binary — can switch freely
        0.05,   # u_fan: 5% per step (VFD ramp rate)
        1.0,    # u_irr: binary — can switch freely
    ]))


@dataclass
class SafetyBounds:
    """Hard safety limits on state variables."""
    t_air_min: float = 15.0         # °C — crop damage below
    t_air_max: float = 35.0         # °C — crop damage above
    co2_max: float = 2000.0         # ppm — safety ceiling
    theta_min: float = 0.10         # m³/m³ — permanent wilt point


@dataclass
class ComfortBands:
    """Soft constraint bands — violation penalized, not prevented."""
    vpd_min: float = 0.8            # kPa
    vpd_max: float = 1.4            # kPa
    t_band: float = 2.0             # °C ±around target
    rh_min: float = 40.0            # %
    rh_max: float = 70.0            # %
    co2_min_lights_on: float = 800.0  # ppm
    dryback_min: float = -0.025     # m³/m³/hr — max drying rate
    dryback_max: float = -0.005     # m³/m³/hr — min drying rate
    theta_trigger: float = 0.28     # VWC irrigation trigger


class ConstraintBuilder:
    """Constructs constraint matrices for the OSQP QP.

    Decision variable layout:
        z = [u[0], u[1], ..., u[Nc-1], x[1], x[2], ..., x[Np], epsilon]

    Where:
        u[k] has nu=7 elements per step
        x[k] has nx=7 elements per step
        epsilon has n_soft slack variables
    """

    def __init__(
        self,
        nx: int = 7,
        nu: int = 7,
        Np: int = 60,
        Nc: int = 15,
        actuator_limits: ActuatorLimits | None = None,
        rate_limits: RateLimits | None = None,
        safety: SafetyBounds | None = None,
        comfort: ComfortBands | None = None,
    ):
        self.nx = nx
        self.nu = nu
        self.Np = Np
        self.Nc = Nc
        self.limits = actuator_limits or ActuatorLimits()
        self.rates = rate_limits or RateLimits()
        self.safety = safety or SafetyBounds()
        self.comfort = comfort or ComfortBands()

        # Soft constraint count: VPD (2: min/max) × Np steps
        self.n_soft = 2 * Np
        self.n_u = Nc * nu
        self.n_x = Np * nx
        self.n_var = self.n_u + self.n_x + self.n_soft

    def build_bounds(
        self,
        u_prev: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build box constraint vectors (z_lb, z_ub).

        Returns:
            z_lb: Lower bounds on decision variables
            z_ub: Upper bounds on decision variables
        """
        z_lb = np.full(self.n_var, -np.inf)
        z_ub = np.full(self.n_var, np.inf)

        # Actuator box constraints
        for k in range(self.Nc):
            offset = k * self.nu
            z_lb[offset:offset + self.nu] = self.limits.u_min
            z_ub[offset:offset + self.nu] = self.limits.u_max

        # State safety bounds (hard)
        x_offset = self.n_u
        for k in range(self.Np):
            idx = x_offset + k * self.nx
            # T_air bounds
            z_lb[idx + 0] = self.safety.t_air_min
            z_ub[idx + 0] = self.safety.t_air_max
            # CO2 upper bound
            z_ub[idx + 2] = self.safety.co2_max
            # Theta lower bound (wilt point)
            z_lb[idx + 4] = self.safety.theta_min

        # Slack variables are non-negative
        slack_offset = self.n_u + self.n_x
        z_lb[slack_offset:] = 0.0
        z_ub[slack_offset:] = np.inf

        return z_lb, z_ub

    def build_rate_constraints(
        self,
        u_prev: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Build rate-of-change constraint matrices.

        |u[k] - u[k-1]| ≤ du_max for each control step.

        Reformulated as linear:
            -du_max ≤ u[k] - u[k-1] ≤ du_max

        Returns:
            A_rate: Constraint matrix (n_rate_con × n_var)
            l_rate: Lower bound vector
            u_rate: Upper bound vector
        """
        n_rate = self.Nc * self.nu
        A_rate = np.zeros((n_rate, self.n_var))
        l_rate = np.zeros(n_rate)
        u_rate = np.zeros(n_rate)

        du_max = self.rates.du_max

        for k in range(self.Nc):
            for j in range(self.nu):
                row = k * self.nu + j
                col_curr = k * self.nu + j

                if k == 0:
                    # First step: u[0] - u_prev
                    A_rate[row, col_curr] = 1.0
                    l_rate[row] = u_prev[j] - du_max[j]
                    u_rate[row] = u_prev[j] + du_max[j]
                else:
                    col_prev = (k - 1) * self.nu + j
                    A_rate[row, col_curr] = 1.0
                    A_rate[row, col_prev] = -1.0
                    l_rate[row] = -du_max[j]
                    u_rate[row] = du_max[j]

        return A_rate, l_rate, u_rate

    def build_mutual_exclusion(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Build mutual exclusion constraints.

        u_cool + u_heat ≤ 1.0 (no simultaneous heating/cooling)
        u_hum + u_dehum ≤ 1.0 (no simultaneous humidify/dehumidify)

        Returns:
            A_mutex: Constraint matrix (2*Nc × n_var)
            l_mutex: Lower bounds (all -inf)
            u_mutex: Upper bounds (all 1.0)
        """
        n_mutex = 2 * self.Nc
        A_mutex = np.zeros((n_mutex, self.n_var))
        l_mutex = np.full(n_mutex, -np.inf)
        u_mutex = np.ones(n_mutex)

        for k in range(self.Nc):
            base = k * self.nu
            # cool + heat ≤ 1.0
            row1 = 2 * k
            A_mutex[row1, base + 0] = 1.0  # u_cool
            A_mutex[row1, base + 1] = 1.0  # u_heat

            # hum + dehum ≤ 1.0
            row2 = 2 * k + 1
            A_mutex[row2, base + 3] = 1.0  # u_hum
            A_mutex[row2, base + 2] = 1.0  # u_dehum

        return A_mutex, l_mutex, u_mutex

    def build_dynamics_constraints(
        self,
        x0: np.ndarray,
        A_d: np.ndarray,
        B_d: np.ndarray,
        Bd_d: np.ndarray,
        d_forecast: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Build equality constraints for system dynamics.

        x[k+1] = A·x[k] + B·u[k] + Bd·d[k]

        For k=0: x[1] = A·x0 + B·u[0] + Bd·d[0]
        For k>0: x[k+1] = A·x[k] + B·u[k] + Bd·d[k]

        For k ≥ Nc (beyond control horizon), u[k] = u[Nc-1] (hold last input).

        Returns:
            A_dyn: Equality constraint matrix (Np*nx × n_var)
            l_dyn: RHS vector (equality, so l == u)
            u_dyn: RHS vector
        """
        nx, nu, Np, Nc = self.nx, self.nu, self.Np, self.Nc
        n_dyn = Np * nx
        A_dyn = np.zeros((n_dyn, self.n_var))
        rhs = np.zeros(n_dyn)

        x_base = self.n_u  # where state variables start in z

        for k in range(Np):
            row_start = k * nx
            row_end = row_start + nx

            # x[k+1] coefficient: +I
            A_dyn[row_start:row_end, x_base + k * nx:x_base + (k + 1) * nx] = np.eye(nx)

            if k == 0:
                # x[1] = A·x0 + B·u[0] + Bd·d[0]
                # x[1] - B·u[0] = A·x0 + Bd·d[0]
                u_idx = 0  # first control step
                A_dyn[row_start:row_end, u_idx * nu:(u_idx + 1) * nu] = -B_d
                rhs[row_start:row_end] = A_d @ x0 + Bd_d @ d_forecast[0]
            else:
                # x[k+1] = A·x[k] + B·u[min(k,Nc-1)] + Bd·d[k]
                # x[k+1] - A·x[k] - B·u[min(k,Nc-1)] = Bd·d[k]
                prev_x_start = x_base + (k - 1) * nx
                A_dyn[row_start:row_end, prev_x_start:prev_x_start + nx] = -A_d

                u_idx = min(k, Nc - 1)
                A_dyn[row_start:row_end, u_idx * nu:(u_idx + 1) * nu] -= B_d

                rhs[row_start:row_end] = Bd_d @ d_forecast[min(k, len(d_forecast) - 1)]

        return A_dyn, rhs, rhs  # equality: lower == upper

    def build_all(
        self,
        x0: np.ndarray,
        u_prev: np.ndarray,
        A_d: np.ndarray,
        B_d: np.ndarray,
        Bd_d: np.ndarray,
        d_forecast: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Build all constraint matrices combined.

        Returns:
            A: Combined constraint matrix
            l: Combined lower bounds
            u: Combined upper bounds
            z_lb: Box constraint lower bounds
            z_ub: Box constraint upper bounds
        """
        A_dyn, l_dyn, u_dyn = self.build_dynamics_constraints(
            x0, A_d, B_d, Bd_d, d_forecast,
        )
        A_rate, l_rate, u_rate = self.build_rate_constraints(u_prev)
        A_mutex, l_mutex, u_mutex = self.build_mutual_exclusion()
        z_lb, z_ub = self.build_bounds(u_prev)

        # Stack all constraints
        A = np.vstack([A_dyn, A_rate, A_mutex])
        l = np.concatenate([l_dyn, l_rate, l_mutex])
        u = np.concatenate([u_dyn, u_rate, u_mutex])

        return A, l, u, z_lb, z_ub
