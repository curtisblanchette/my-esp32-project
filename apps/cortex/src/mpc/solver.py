"""OSQP-based MPC solver with warm-starting and variable scaling.

Constructs and solves the QP at each control cycle:
    minimize    (1/2)·z'·P·z + q'·z
    subject to  A·z ∈ [l, u]
                z_lb ≤ z ≤ z_ub

Variable scaling normalizes states (which span different orders of magnitude)
to ~O(1) for well-conditioned QP matrices and fast OSQP convergence.

Target solve time: < 100ms (95th percentile) on Raspberry Pi 5.
Typical warm-started solve: < 10ms.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sparse

try:
    import osqp
    HAS_OSQP = True
except ImportError:
    HAS_OSQP = False

from .model import GrowRoomModel, NX, NU, ND, RoomParams
from .constraints import ConstraintBuilder, ActuatorLimits, RateLimits, SafetyBounds
from .reference import ReferenceGenerator, CostWeights

logger = logging.getLogger(__name__)

# Variable scaling factors: map physical units to ~O(1)
# Scaled = physical / scale
STATE_SCALE = np.array([
    10.0,   # T_air: 25°C → 2.5 scaled
    10.0,   # w_air: 10 g/kg → 1.0 scaled
    500.0,  # CO2: 1000 ppm → 2.0 scaled
    10.0,   # T_leaf: 25°C → 2.5 scaled
    0.5,    # theta: 0.35 → 0.7 scaled
    10.0,   # T_supply: 20°C → 2.0 scaled
    10.0,   # T_wall: 23°C → 2.3 scaled
])


@dataclass
class MPCSolveResult:
    """Result of a single MPC solve cycle."""
    u_optimal: np.ndarray           # First control action to apply (7,)
    predicted_trajectory: np.ndarray | None  # Full state prediction (Np, 7)
    total_ms: float = 0.0
    linearize_ms: float = 0.0
    build_qp_ms: float = 0.0
    solve_ms: float = 0.0
    iterations: int = 0
    status: str = "unsolved"
    feasible: bool = False


@dataclass
class MPCConfig:
    """Configuration for the OSQP MPC solver."""
    Np: int = 30                    # prediction horizon (steps)
    Nc: int = 10                    # control horizon (steps)
    dt: float = 60.0                # prediction step (seconds)

    # OSQP settings
    eps_abs: float = 1e-3
    eps_rel: float = 1e-3
    max_iter: int = 500
    polish: bool = True
    adaptive_rho: bool = True
    warm_start: bool = True
    verbose: bool = False
    scaling: int = 20               # OSQP scaling iterations


class MPCSolver:
    """OSQP-based Model Predictive Controller.

    Encapsulates the full MPC pipeline:
    1. Linearize model around current state
    2. Build QP matrices (cost + constraints) with variable scaling
    3. Solve with OSQP (warm-started)
    4. Extract first control action (unscaled to physical units)
    """

    def __init__(
        self,
        model: GrowRoomModel | None = None,
        config: MPCConfig | None = None,
        weights: CostWeights | None = None,
        room_params: RoomParams | None = None,
    ):
        if not HAS_OSQP:
            raise ImportError(
                "osqp is required for MPCSolver. Install with: pip install osqp"
            )

        self.model = model or GrowRoomModel(room_params)
        self.cfg = config or MPCConfig()
        self.weights = weights or CostWeights()

        self.nx = NX
        self.nu = NU
        self.Np = self.cfg.Np
        self.Nc = self.cfg.Nc
        self.dt = self.cfg.dt

        # Variable scaling
        self._x_scale = STATE_SCALE.copy()
        self._x_scale_inv = 1.0 / self._x_scale

        # Constraint builder
        self.constraints = ConstraintBuilder(
            nx=NX, nu=NU, Np=self.Np, Nc=self.Nc,
        )

        # Decision variable dimensions
        self.n_u = self.Nc * NU
        self.n_x = self.Np * NX
        self.n_soft = self.constraints.n_soft
        self.n_var = self.n_u + self.n_x + self.n_soft

        # OSQP solver instance (lazy init on first solve)
        self._solver = None
        self._initialized = False

        # Warm-start state
        self._last_solution: np.ndarray | None = None
        self._last_u: np.ndarray | None = None
        self._last_dual: np.ndarray | None = None

    def solve(
        self,
        x0: np.ndarray,
        d_forecast: np.ndarray,
        reference: np.ndarray,
        u_prev: np.ndarray | None = None,
    ) -> MPCSolveResult:
        """Main MPC solve function.

        Args:
            x0: Current state estimate (7,) in physical units
            d_forecast: Disturbance forecast (Np × nd)
            reference: State reference trajectory (Np × nx) in physical units
            u_prev: Previous control action (7,) for rate constraints

        Returns:
            MPCSolveResult with optimal control action and diagnostics
        """
        t_start = time.perf_counter_ns()

        if u_prev is None:
            u_prev = self._last_u if self._last_u is not None else self.model.default_input()

        # Ensure forecast covers full horizon
        if len(d_forecast) < self.Np:
            pad = np.tile(d_forecast[-1:], (self.Np - len(d_forecast), 1))
            d_forecast = np.vstack([d_forecast, pad])

        # --- Step 1: Linearize (in physical units) ---
        A_d, B_d, Bd_d = self.model.linearize(x0, u_prev, d_forecast[0], self.dt)
        t_linearize = time.perf_counter_ns()

        # Scale linearization matrices: x_s = x/S, so
        # x_s[k+1] = S_inv @ A_d @ S · x_s[k] + S_inv @ B_d · u[k] + S_inv @ Bd_d · d[k]
        S = np.diag(self._x_scale)
        S_inv = np.diag(self._x_scale_inv)
        A_d_s = S_inv @ A_d @ S
        B_d_s = S_inv @ B_d
        Bd_d_s = S_inv @ Bd_d

        # Scale reference and initial state
        x0_s = x0 * self._x_scale_inv
        ref_s = reference * self._x_scale_inv[np.newaxis, :]

        # --- Step 2: Build QP ---
        P, q, A, l, u, z_lb, z_ub = self._build_qp(
            x0_s, u_prev, A_d_s, B_d_s, Bd_d_s, d_forecast, ref_s,
        )
        t_build = time.perf_counter_ns()

        # --- Step 3: Solve with OSQP ---
        try:
            result = self._solve_osqp(P, q, A, l, u, z_lb, z_ub)
        except Exception as e:
            logger.warning(f"OSQP solve failed: {e}")
            return MPCSolveResult(
                u_optimal=u_prev.copy(),
                predicted_trajectory=None,
                status="failed",
            )
        t_solve = time.perf_counter_ns()

        # --- Step 4: Extract solution ---
        solved = result.info.status in ("solved", "solved_inaccurate")
        if solved:
            z_opt = result.x
            u_optimal = z_opt[:NU].copy()
            u_optimal = np.clip(u_optimal, 0.0, 1.0)

            # Extract and unscale predicted trajectory
            x_traj_s = z_opt[self.n_u:self.n_u + self.n_x].reshape(self.Np, NX)
            x_traj = x_traj_s * self._x_scale[np.newaxis, :]

            # Save for warm-starting
            self._last_solution = z_opt.copy()
            self._last_u = u_optimal.copy()
            if hasattr(result, 'y') and result.y is not None:
                self._last_dual = result.y.copy()
        else:
            u_optimal = u_prev.copy()
            x_traj = None
            logger.warning(f"MPC solve status: {result.info.status}, holding last u")

        t_extract = time.perf_counter_ns()

        return MPCSolveResult(
            u_optimal=u_optimal,
            predicted_trajectory=x_traj,
            total_ms=(t_extract - t_start) / 1e6,
            linearize_ms=(t_linearize - t_start) / 1e6,
            build_qp_ms=(t_build - t_linearize) / 1e6,
            solve_ms=(t_solve - t_build) / 1e6,
            iterations=result.info.iter if solved else 0,
            status=result.info.status,
            feasible=solved,
        )

    def _build_qp(
        self,
        x0_s: np.ndarray,
        u_prev: np.ndarray,
        A_d_s: np.ndarray,
        B_d_s: np.ndarray,
        Bd_d_s: np.ndarray,
        d_forecast: np.ndarray,
        ref_s: np.ndarray,
    ) -> tuple[sparse.csc_matrix, np.ndarray, sparse.csc_matrix, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Construct QP matrices for OSQP in scaled coordinates.

        Cost: J = (1/2)·z'·P·z + q'·z
        where z = [u_flat, x_s_flat, epsilon]
        """
        n_var = self.n_var
        Np, Nc = self.Np, self.Nc
        nx, nu = self.nx, self.nu

        # --- Cost matrix P (block diagonal) ---
        P_diag = np.zeros(n_var)

        # Input cost: R
        R_diag = self.weights.R_diag()
        for k in range(Nc):
            P_diag[k * nu:(k + 1) * nu] = R_diag

        # Input smoothness: 2·S on diagonal (approximation of Δu'·S·Δu)
        S_diag = self.weights.S_diag()
        for k in range(Nc):
            P_diag[k * nu:(k + 1) * nu] += 2.0 * S_diag

        # State tracking cost: Q_scaled = Q * scale² (so cost is scale-invariant)
        Q_diag = self.weights.Q_diag()
        Q_diag_s = Q_diag * self._x_scale ** 2  # compensate for scaled variables
        for k in range(Np):
            offset = self.n_u + k * nx
            P_diag[offset:offset + nx] = Q_diag_s

        # Soft constraint penalty
        slack_offset = self.n_u + self.n_x
        P_diag[slack_offset:] = self.weights.soft_penalty

        # Ensure positive definite (add small regularization)
        P_diag = np.maximum(P_diag, 1e-6)
        P = sparse.diags(P_diag, format='csc')

        # --- Linear cost vector q ---
        q = np.zeros(n_var)

        # State tracking: q_x = -Q_s · ref_s
        for k in range(Np):
            offset = self.n_u + k * nx
            q[offset:offset + nx] = -Q_diag_s * ref_s[k]

        # Rate-of-change: first step penalizes deviation from u_prev
        q[:nu] -= 2.0 * S_diag * u_prev

        # --- Build constraints in scaled coordinates ---
        # Dynamics constraints
        A_dyn, l_dyn, u_dyn = self._build_scaled_dynamics(
            x0_s, A_d_s, B_d_s, Bd_d_s, d_forecast,
        )

        # Rate constraints (inputs are not scaled)
        A_rate, l_rate, u_rate = self.constraints.build_rate_constraints(u_prev)

        # Mutual exclusion constraints
        A_mutex, l_mutex, u_mutex = self.constraints.build_mutual_exclusion()

        # Stack all constraints
        A_con = np.vstack([A_dyn, A_rate, A_mutex])
        l_con = np.concatenate([l_dyn, l_rate, l_mutex])
        u_con = np.concatenate([u_dyn, u_rate, u_mutex])

        # Box bounds (scale state bounds)
        z_lb, z_ub = self._build_scaled_bounds()

        A_sparse = sparse.csc_matrix(A_con)
        return P, q, A_sparse, l_con, u_con, z_lb, z_ub

    def _build_scaled_dynamics(
        self,
        x0_s: np.ndarray,
        A_d_s: np.ndarray,
        B_d_s: np.ndarray,
        Bd_d_s: np.ndarray,
        d_forecast: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Build dynamics constraints in scaled coordinates."""
        nx, nu, Np, Nc = self.nx, self.nu, self.Np, self.Nc
        n_dyn = Np * nx
        A_dyn = np.zeros((n_dyn, self.n_var))
        rhs = np.zeros(n_dyn)

        x_base = self.n_u

        for k in range(Np):
            row_s = k * nx
            row_e = row_s + nx

            # x_s[k+1] coefficient: +I
            A_dyn[row_s:row_e, x_base + k * nx:x_base + (k + 1) * nx] = np.eye(nx)

            if k == 0:
                # x_s[1] = A_s·x0_s + B_s·u[0] + Bd_s·d[0]
                u_idx = 0
                A_dyn[row_s:row_e, u_idx * nu:(u_idx + 1) * nu] = -B_d_s
                rhs[row_s:row_e] = A_d_s @ x0_s + Bd_d_s @ d_forecast[0]
            else:
                prev_x = x_base + (k - 1) * nx
                A_dyn[row_s:row_e, prev_x:prev_x + nx] = -A_d_s
                u_idx = min(k, Nc - 1)
                A_dyn[row_s:row_e, u_idx * nu:(u_idx + 1) * nu] -= B_d_s
                rhs[row_s:row_e] = Bd_d_s @ d_forecast[min(k, len(d_forecast) - 1)]

        return A_dyn, rhs, rhs

    def _build_scaled_bounds(self) -> tuple[np.ndarray, np.ndarray]:
        """Build box constraints in scaled coordinates."""
        z_lb = np.full(self.n_var, -np.inf)
        z_ub = np.full(self.n_var, np.inf)

        safety = self.constraints.safety
        limits = self.constraints.limits

        # Input bounds (not scaled)
        for k in range(self.Nc):
            offset = k * self.nu
            z_lb[offset:offset + self.nu] = limits.u_min
            z_ub[offset:offset + self.nu] = limits.u_max

        # State bounds (scaled)
        x_offset = self.n_u
        for k in range(self.Np):
            idx = x_offset + k * self.nx
            z_lb[idx + 0] = safety.t_air_min / self._x_scale[0]
            z_ub[idx + 0] = safety.t_air_max / self._x_scale[0]
            z_ub[idx + 2] = safety.co2_max / self._x_scale[2]
            z_lb[idx + 4] = safety.theta_min / self._x_scale[4]

        # Slack variables ≥ 0
        slack_offset = self.n_u + self.n_x
        z_lb[slack_offset:] = 0.0

        return z_lb, z_ub

    def _solve_osqp(
        self,
        P: sparse.csc_matrix,
        q: np.ndarray,
        A: sparse.csc_matrix,
        l: np.ndarray,
        u: np.ndarray,
        z_lb: np.ndarray,
        z_ub: np.ndarray,
    ):
        """Set up and solve the QP with OSQP."""
        n_var = P.shape[0]

        # Box constraints as additional rows: I·z ∈ [z_lb, z_ub]
        I_box = sparse.eye(n_var, format='csc')
        A_full = sparse.vstack([A, I_box], format='csc')
        l_full = np.concatenate([l, z_lb])
        u_full = np.concatenate([u, z_ub])

        if not self._initialized:
            self._solver = osqp.OSQP()
            self._solver.setup(
                P=sparse.triu(P, format='csc'),
                q=q,
                A=A_full,
                l=l_full,
                u=u_full,
                warm_starting=self.cfg.warm_start,
                verbose=self.cfg.verbose,
                eps_abs=self.cfg.eps_abs,
                eps_rel=self.cfg.eps_rel,
                max_iter=self.cfg.max_iter,
                polishing=self.cfg.polish,
                adaptive_rho=self.cfg.adaptive_rho,
                scaling=self.cfg.scaling,
            )
            self._initialized = True
        else:
            # Re-setup with new data (sparsity pattern may change each cycle)
            self._solver = osqp.OSQP()
            self._solver.setup(
                P=sparse.triu(P, format='csc'),
                q=q,
                A=A_full,
                l=l_full,
                u=u_full,
                warm_starting=self.cfg.warm_start,
                verbose=self.cfg.verbose,
                eps_abs=self.cfg.eps_abs,
                eps_rel=self.cfg.eps_rel,
                max_iter=self.cfg.max_iter,
                polishing=self.cfg.polish,
                adaptive_rho=self.cfg.adaptive_rho,
                scaling=self.cfg.scaling,
            )

        # Warm-start
        if self._last_solution is not None and self.cfg.warm_start:
            x_warm = self._shift_warmstart(self._last_solution)
            try:
                if self._last_dual is not None and len(self._last_dual) == len(l_full):
                    self._solver.warm_start(x=x_warm, y=self._last_dual)
                else:
                    self._solver.warm_start(x=x_warm)
            except Exception:
                pass

        return self._solver.solve()

    def _shift_warmstart(self, prev_solution: np.ndarray) -> np.ndarray:
        """Shift previous solution forward by one step for warm-starting."""
        z = prev_solution.copy()
        nu, nx = self.nu, self.nx
        Nc, Np = self.Nc, self.Np

        if Nc > 1:
            z[:self.n_u - nu] = prev_solution[nu:self.n_u]
            z[self.n_u - nu:self.n_u] = prev_solution[self.n_u - nu:self.n_u]

        x_start = self.n_u
        if Np > 1:
            z[x_start:x_start + self.n_x - nx] = prev_solution[x_start + nx:x_start + self.n_x]
            z[x_start + self.n_x - nx:x_start + self.n_x] = prev_solution[x_start + self.n_x - nx:x_start + self.n_x]

        return z

    def reset(self) -> None:
        """Reset solver state (clear warm-start, force re-init)."""
        self._solver = None
        self._initialized = False
        self._last_solution = None
        self._last_u = None
        self._last_dual = None
