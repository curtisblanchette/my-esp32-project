"""MPC (Model Predictive Control) state planner for environment simulation.

Uses direct nonlinear shooting with the actual PhysicsEngine as the prediction
model.  At each control step the planner:

1. ``deepcopy``'s the environment
2. Parameterises actuator intensities over *N* control intervals
3. Rolls the clone forward, evaluating a weighted cost function
4. Calls ``scipy.optimize.minimize`` (SLSQP) to find the optimal trajectory
5. Applies only the first control interval (receding horizon)

Cost = w_goal × J_goal  +  w_energy × J_energy  +  w_rate × J_rate
"""

import copy
import logging
import time as _time
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

from .physics import ACTUATOR_SPECS, PhysicsEngine

logger = logging.getLogger(__name__)

# Default actuator ordering (used by module-level constants; MPCPlanner
# derives its own list from per-room actuator specs at init time).
DEFAULT_ACTUATOR_NAMES: list[str] = [
    "fan", "exhaust_fan", "humidifier", "dehumidifier",
    "irrigation", "light", "co2_injector",
]

# Backward-compat aliases (tests import these)
ACTUATOR_NAMES = DEFAULT_ACTUATOR_NAMES
NUM_ACTUATORS = len(ACTUATOR_NAMES)

# Pre-compute max-watts vector in DEFAULT order
_RELAY_FOR_NAME = {spec.name: spec for spec in ACTUATOR_SPECS.values()}
WATTS = np.array([_RELAY_FOR_NAME[n].max_watts for n in DEFAULT_ACTUATOR_NAMES])
MAX_POWER = float(WATTS.sum())


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MPCConfig:
    """Configuration for the MPC state planner."""

    # Horizon & timing
    horizon_minutes: float = 15.0
    control_interval_s: float = 150.0   # 2.5 min per control interval

    # Cost weights
    w_goal: float = 1.0
    w_energy: float = 0.05
    w_rate: float = 0.1

    # Optimizer
    max_iterations: int = 100
    warm_max_iterations: int = 20   # reduced iteration budget when warm-starting
    method: str = "SLSQP"

    # Hard constraints
    light_schedule: dict | None = field(
        default_factory=lambda: {"on_hour": 6, "off_hour": 22}
    )

    # Warm start
    warm_start: bool = True

    @property
    def horizon_steps(self) -> int:
        """Number of physics steps (at step_seconds=30 s) in the horizon."""
        return max(1, int(self.horizon_minutes * 60 / 30.0))

    @property
    def num_control_intervals(self) -> int:
        """Number of control intervals to optimise."""
        return max(1, int(self.horizon_minutes * 60 / self.control_interval_s))

    @property
    def steps_per_interval(self) -> int:
        """Physics steps per control interval."""
        return max(1, int(self.control_interval_s / 30.0))

    @classmethod
    def auto_scale(cls, base: "MPCConfig", volume_m3: float) -> "MPCConfig":
        """Return a new MPCConfig with weights/horizon scaled for room volume.

        Scaling is relative to a reference tent volume (2.88 m³).  Larger
        rooms have more thermal mass → need longer horizons, and the
        energy/rate penalties should be lighter so the optimizer can
        act aggressively enough to keep up with the slower dynamics.

        Scaling rules (capped at 4×):
          ratio   = volume / 2.88
          horizon : × min(√ratio, 4)
          w_energy: / √ratio
          w_rate  : / √ratio
        """
        _REFERENCE_VOLUME = 2.88
        if volume_m3 <= _REFERENCE_VOLUME:
            return base
        ratio = volume_m3 / _REFERENCE_VOLUME
        sqrt_ratio = ratio ** 0.5
        scale = min(sqrt_ratio, 4.0)  # cap at 4×

        return cls(
            horizon_minutes=base.horizon_minutes * scale,
            control_interval_s=base.control_interval_s * scale,
            w_goal=base.w_goal,
            w_energy=base.w_energy / sqrt_ratio,
            w_rate=base.w_rate / sqrt_ratio,
            max_iterations=base.max_iterations,
            warm_max_iterations=base.warm_max_iterations,
            method=base.method,
            light_schedule=base.light_schedule,
            warm_start=base.warm_start,
        )


@dataclass
class GoalSpec:
    """A single goal for the MPC cost function."""

    metric: str
    target: float
    range_min: float
    range_max: float
    tolerance: float
    weight: float
    on_hour: float | None = None   # start of active window (None = always active)
    off_hour: float | None = None  # end of active window

    @property
    def range_width(self) -> float:
        return max(self.range_max - self.range_min, 1e-6)


def goals_from_dicts(
    goal_dicts: list[dict],
    strategy: str = "balanced",
) -> list[GoalSpec]:
    """Convert scenario goal dicts to GoalSpec objects.

    Supports optional ``timeWindow`` with ``onHour``/``offHour`` for
    day/night-aware goal scheduling.
    """
    specs: list[GoalSpec] = []
    for g in goal_dicts:
        rmin = g.get("rangeMin", 0.0)
        rmax = g.get("rangeMax", 100.0)
        tw = g.get("timeWindow")
        on_hour = tw["onHour"] if tw else None
        off_hour = tw["offHour"] if tw else None
        specs.append(GoalSpec(
            metric=g["metric"],
            target=(rmin + rmax) / 2.0,
            range_min=rmin,
            range_max=rmax,
            tolerance=g.get("tolerance", 1.0),
            weight=g.get("priority", 1.0),
            on_hour=on_hour,
            off_hour=off_hour,
        ))
    return specs


@dataclass
class MPCResult:
    """Result of a single MPC solve step."""

    optimal_intensities: dict[str, float]
    predicted_trajectory: list[dict[str, float]]
    cost: float
    goal_cost: float
    energy_cost: float
    rate_cost: float
    solve_time_ms: float
    num_iterations: int
    success: bool


@dataclass
class _WarmState:
    """Persistent state across solves for warm-starting."""

    previous_solution: np.ndarray | None = None
    previous_intensities: np.ndarray | None = None


# ---------------------------------------------------------------------------
# MPC Planner
# ---------------------------------------------------------------------------

class MPCPlanner:
    """Model Predictive Controller using direct nonlinear shooting."""

    def __init__(
        self,
        config: MPCConfig,
        goals: list[GoalSpec],
        actuator_specs: dict | None = None,
    ) -> None:
        self._cfg = config
        self._goals = goals
        self._warm = _WarmState()

        # Build per-instance actuator list from specs (canonical order first,
        # extras like "hvac" appended).  This makes the planner work with
        # any room's actuator set without hardcoding names.
        specs = actuator_specs or ACTUATOR_SPECS
        self._spec_by_name: dict = {s.name: s for s in specs.values()}
        ordered: list[str] = []
        for name in DEFAULT_ACTUATOR_NAMES:
            if name in self._spec_by_name:
                ordered.append(name)
        for name in self._spec_by_name:
            if name not in ordered:
                ordered.append(name)
        self._actuator_names: list[str] = ordered
        self._num_actuators: int = len(ordered)
        self._ndim = config.num_control_intervals * self._num_actuators

        self._watts = np.array([self._spec_by_name[n].max_watts for n in self._actuator_names])
        self._max_power = float(self._watts.sum())

    @property
    def actuator_names(self) -> list[str]:
        """Ordered actuator names for this planner (may include extras like hvac)."""
        return list(self._actuator_names)

    # ── public API ────────────────────────────────────────────────────

    def solve(
        self,
        environment: PhysicsEngine,
        current_hour: float,
    ) -> MPCResult:
        """Find optimal actuator intensities for the next control interval."""
        t0 = _time.perf_counter()

        n_int = self._cfg.num_control_intervals
        n_act = self._num_actuators
        bounds = self._build_bounds(current_hour)

        # Current intensities vector (for rate-of-change penalty)
        cur = np.array([getattr(environment, n) for n in self._actuator_names])

        # Warm-start detection
        is_warm = self._cfg.warm_start and self._warm.previous_solution is not None

        # Initial guess
        if is_warm:
            x0 = self._shift_warm_start(self._warm.previous_solution, n_int)
        else:
            # Start from current intensities repeated
            x0 = np.tile(cur, n_int)

        # Clip x0 to bounds
        for i, (lo, hi) in enumerate(bounds):
            x0[i] = np.clip(x0[i], lo, hi)

        # One-time deep clone for this solve; all objective evaluations
        # reuse it via save_state/restore_state (eliminates ~43+ deepcopy
        # calls per gradient step).
        clone = copy.deepcopy(environment)
        clone.noise = False
        clone._relax_quantization = True
        snapshot = clone.save_state()

        # Cost function closure — restores snapshot before each rollout
        def objective(u: np.ndarray) -> float:
            clone.restore_state(snapshot)
            cost, _ = self._evaluate(u, clone, current_hour, cur)
            return cost

        # Adaptive iteration limit: warm-started solves converge faster
        max_iter = self._cfg.warm_max_iterations if is_warm else self._cfg.max_iterations

        result = minimize(
            objective,
            x0,
            method=self._cfg.method,
            bounds=bounds,
            options={
                "maxiter": max_iter,
                "disp": False,
                # The physics engine needs a finite-difference step large
                # enough for actuator changes to propagate through the ODE.
                # Default eps (~1e-8) is below the physics resolution.
                "eps": 0.01,
            },
        )

        # Extract first-interval intensities (quantized by control type)
        u_opt = result.x
        first = u_opt[:n_act]
        intensities = {}
        for i in range(n_act):
            name = self._actuator_names[i]
            raw = float(np.clip(first[i], 0.0, 1.0))
            spec = self._spec_by_name.get(name)
            intensities[name] = spec.quantize(raw) if spec else raw

        # Decompose costs at the optimum
        clone.restore_state(snapshot)
        _, trajectory = self._evaluate(u_opt, clone, current_hour, cur)
        g_cost = self._goal_cost(trajectory)
        e_cost = self._energy_cost(u_opt)
        r_cost = self._rate_cost(u_opt, cur)

        # Save warm state
        self._warm.previous_solution = u_opt.copy()
        self._warm.previous_intensities = first.copy()

        elapsed_ms = (_time.perf_counter() - t0) * 1000.0

        # Extract readings-only for the result (strip hour from tuples)
        predicted = [readings for readings, _h in trajectory]

        return MPCResult(
            optimal_intensities=intensities,
            predicted_trajectory=predicted,
            cost=float(result.fun),
            goal_cost=g_cost,
            energy_cost=e_cost,
            rate_cost=r_cost,
            solve_time_ms=elapsed_ms,
            num_iterations=result.nit if hasattr(result, "nit") else 0,
            success=result.success,
        )

    def reset_warm_start(self) -> None:
        """Clear warm-start state (e.g. after a large disturbance)."""
        self._warm = _WarmState()

    # ── internal ─────────────────────────────────────────────────────

    def _evaluate(
        self,
        u: np.ndarray,
        env: PhysicsEngine,
        current_hour: float,
        current_intensities: np.ndarray,
    ) -> tuple[float, list[tuple[dict[str, float], float]]]:
        """Roll out control sequence and compute cost."""
        trajectory = self._rollout(u, env, current_hour)
        cost = (
            self._cfg.w_goal * self._goal_cost(trajectory)
            + self._cfg.w_energy * self._energy_cost(u)
            + self._cfg.w_rate * self._rate_cost(u, current_intensities)
        )
        return cost, trajectory

    def _rollout(
        self,
        u: np.ndarray,
        env: PhysicsEngine,
        current_hour: float,
    ) -> list[tuple[dict[str, float], float]]:
        """Step env through the control horizon in-place.

        The caller (solve → objective) is responsible for restoring env's
        state before each call via save_state/restore_state.

        Returns a list of ``(readings, hour)`` tuples — one per control
        interval — so the cost function can filter goals by time-of-day.
        """
        n_int = self._cfg.num_control_intervals
        spi = self._cfg.steps_per_interval
        trajectory: list[tuple[dict[str, float], float]] = []

        hour = current_hour
        step_hours = 30.0 / 3600.0  # 30 s in fractional hours

        n_act = self._num_actuators
        for k in range(n_int):
            # Set actuators for this interval (quantized by control type)
            uk = u[k * n_act: (k + 1) * n_act]
            for i, name in enumerate(self._actuator_names):
                env.set_actuator(name, float(np.clip(uk[i], 0.0, 1.0)))

            # Step physics
            for _ in range(spi):
                env.step(30.0, current_hour=hour)
                hour = (hour + step_hours) % 24.0

            trajectory.append((env.get_readings(), hour))

        return trajectory

    def _goal_cost(
        self, trajectory: list[tuple[dict[str, float], float]],
    ) -> float:
        """Quadratic penalty for readings outside goal bands.

        Each trajectory entry is ``(readings, hour)``.  Goals with a
        ``timeWindow`` (``on_hour``/``off_hour``) are only evaluated
        when the step hour falls within that window.
        """
        if not trajectory:
            return 0.0
        total = 0.0
        for readings, hour in trajectory:
            for g in self._goals:
                # Skip goals not active at this hour
                if g.on_hour is not None and not hour_in_window(
                    hour, g.on_hour, g.off_hour,
                ):
                    continue
                val = readings.get(g.metric)
                if val is None:
                    continue
                if g.range_min <= val <= g.range_max:
                    continue
                if val < g.range_min:
                    dev = (g.range_min - val) / g.range_width
                else:
                    dev = (val - g.range_max) / g.range_width
                total += g.weight * dev * dev
        return total / len(trajectory)

    def _energy_cost(self, u: np.ndarray) -> float:
        """Normalised power consumption averaged over intervals."""
        n_int = self._cfg.num_control_intervals
        n_act = self._num_actuators
        total = 0.0
        for k in range(n_int):
            uk = u[k * n_act: (k + 1) * n_act]
            power = float(np.dot(np.clip(uk, 0, 1), self._watts))
            total += power / self._max_power
        return total / max(n_int, 1)

    def _rate_cost(
        self,
        u: np.ndarray,
        current_intensities: np.ndarray,
    ) -> float:
        """Penalty for actuator rate-of-change (chattering)."""
        n_int = self._cfg.num_control_intervals
        n_act = self._num_actuators
        prev = current_intensities
        total = 0.0
        for k in range(n_int):
            uk = u[k * n_act: (k + 1) * n_act]
            diff = uk - prev
            total += float(np.dot(diff, diff))
            prev = uk
        return total / max(n_int, 1)

    def _build_bounds(
        self,
        current_hour: float,
    ) -> list[tuple[float, float]]:
        """Build per-variable bounds with light/CO2 schedule constraints."""
        n_int = self._cfg.num_control_intervals
        n_act = self._num_actuators
        spi = self._cfg.steps_per_interval
        step_hours = 30.0 / 3600.0
        bounds: list[tuple[float, float]] = []

        light_idx = self._actuator_names.index("light") if "light" in self._actuator_names else -1
        co2_idx = self._actuator_names.index("co2_injector") if "co2_injector" in self._actuator_names else -1

        hour = current_hour
        for k in range(n_int):
            # Compute the hour at the START of this interval
            interval_hour = hour % 24.0

            for a in range(n_act):
                lo, hi = 0.0, 1.0
                if self._cfg.light_schedule and a in (light_idx, co2_idx):
                    on_h = self._cfg.light_schedule["on_hour"]
                    off_h = self._cfg.light_schedule["off_hour"]
                    if not hour_in_window(interval_hour, on_h, off_h):
                        hi = 0.0  # force off during darkness
                bounds.append((lo, hi))

            # Advance hour by the interval duration
            hour += spi * step_hours

        return bounds

    def _shift_warm_start(
        self,
        prev: np.ndarray,
        n_intervals: int,
    ) -> np.ndarray:
        """Shift previous solution by one interval for warm-starting."""
        n_act = self._num_actuators
        # Drop first interval, duplicate last interval
        shifted = np.empty_like(prev)
        start = n_act
        shifted[: (n_intervals - 1) * n_act] = prev[start:]
        # Last interval: repeat the last interval from prev
        shifted[(n_intervals - 1) * n_act:] = prev[
            (n_intervals - 1) * n_act:
        ]
        return shifted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hour_in_window(hour: float, on_hour: float, off_hour: float) -> bool:
    """Check if *hour* falls within [on_hour, off_hour).

    Handles wrap-around (e.g. on=22, off=6 means night schedule).
    """
    if on_hour < off_hour:
        return on_hour <= hour < off_hour
    else:
        # Wrap-around (e.g. on=22 off=6 → on from 22-24 and 0-6)
        return hour >= on_hour or hour < off_hour
