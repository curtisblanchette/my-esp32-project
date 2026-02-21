"""Simulation runner — MPC-only time-stepping loop.

Steps the physics environment forward, runs the MPC planner to compute
optimal actuator intensities, applies them back to the environment,
and records all state for visualization.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

from .physics import PhysicsEngine, SpaceConfig, PlantModel

# Sentinel to distinguish "not passed" from "explicitly None"
_UNSET: Any = object()

logger = logging.getLogger(__name__)


# ── MPC Simulation ──────────────────────────────────────────────────


@dataclass
class MPCSimulationResult:
    """Complete output of an MPC-controlled simulation run."""

    timestamps: list[float] = field(default_factory=list)
    readings: dict[str, list[float]] = field(default_factory=dict)
    intensities: dict[str, list[float]] = field(default_factory=dict)
    duration_minutes: int = 0

    # MPC diagnostics
    solve_times_ms: list[float] = field(default_factory=list)
    costs: list[float] = field(default_factory=list)
    goal_costs: list[float] = field(default_factory=list)
    energy_costs: list[float] = field(default_factory=list)
    rate_costs: list[float] = field(default_factory=list)
    health_scores: list[tuple[float, float]] = field(default_factory=list)

    # Energy tracking
    total_energy_wh: float = 0.0
    energy_breakdown: dict[str, float] = field(default_factory=dict)

    # Goal compliance (% time in range per metric)
    compliance: dict[str, float] = field(default_factory=dict)
    avg_health: float = 0.0


def _compute_goal_compliance(
    readings: dict[str, list[float]],
    goals: list[dict],
    start_hour: float,
    step_seconds: int,
    start_idx: int = 0,
    end_idx: int | None = None,
) -> dict[str, float]:
    """Compute per-metric % time in goal range (time-window aware).

    Reusable by both ``run_mpc()`` and ``run_multi_day()``.
    """
    from .state_planner import hour_in_window as _hour_in_window

    # Determine slice bounds
    any_metric = next(iter(readings), None)
    if any_metric is None:
        return {}
    total_len = len(readings[any_metric])
    if end_idx is None:
        end_idx = total_len

    # Build hour series for the slice
    hour_series = [
        (start_hour + i * step_seconds / 3600.0) % 24.0
        for i in range(total_len)
    ]

    metric_in_range: dict[str, int] = {}
    metric_total: dict[str, int] = {}

    for step_idx in range(start_idx, end_idx):
        if step_idx >= total_len:
            break
        hour = hour_series[step_idx]
        for g in goals:
            metric = g["metric"]
            tw = g.get("timeWindow")
            if tw and not _hour_in_window(hour, tw["onHour"], tw["offHour"]):
                continue
            values = readings.get(metric, [])
            if step_idx >= len(values):
                continue
            lo, hi = g.get("rangeMin", 0), g.get("rangeMax", 100)
            metric_total[metric] = metric_total.get(metric, 0) + 1
            if lo <= values[step_idx] <= hi:
                metric_in_range[metric] = metric_in_range.get(metric, 0) + 1

    result: dict[str, float] = {}
    for metric in metric_total:
        total = metric_total[metric]
        in_range = metric_in_range.get(metric, 0)
        result[metric] = round(in_range / total, 4) if total else 0.0
    return result


# ── Multi-Day MPC Simulation ─────────────────────────────────────


@dataclass
class PhaseResult:
    """Results from a single MPC checkpoint phase."""
    phase_num: int
    start_minutes: float
    end_minutes: float
    compliance: dict[str, float]       # per-metric % time in range
    avg_compliance: float              # weighted average across metrics
    energy_wh: float                   # total energy in this phase
    avg_solve_time_ms: float           # average MPC solve time
    avg_cost: float                    # average MPC total cost


@dataclass
class MultiDayResult:
    """Complete output of a multi-day MPC simulation."""
    total_duration_minutes: int
    num_checkpoints: int
    phases: list[PhaseResult]
    # Full continuous timeseries
    timestamps: list[float]
    readings: dict[str, list[float]]
    intensities: dict[str, list[float]]
    # MPC diagnostics (per-step)
    solve_times_ms: list[float]
    costs: list[float]
    goal_costs: list[float]
    energy_costs: list[float]
    rate_costs: list[float]
    # Energy
    total_energy_wh: float
    energy_breakdown: dict[str, float]
    # Overall compliance
    compliance: dict[str, float]
    # Chart compat — populated with avg_compliance per phase
    effectiveness_trajectory: list[float]
    overall_improvement: float


def run_multi_day(
    goals: list[dict],
    profile: dict,
    mpc_config: dict | None = None,
    total_duration_minutes: int = 5760,   # 96h = 4 days
    checkpoint_interval_minutes: int = 720,  # 12h
    step_seconds: int = 30,
    start_hour: int = 8,
    use_ambient_schedule: bool = True,
    actuator_specs: dict | None = None,
    ventilation_fn: Callable[[float], float] | None = None,
    substrate_fn: Callable | None = None,
    substrate_config: Any = None,
    substrate_container: Any = None,
    space: SpaceConfig | None = None,
    plant: PlantModel | None = None,
    ambient_temp: float = 30.0,
    ambient_schedule: Any = _UNSET,
    initial_conditions: dict | None = None,
    fast_physics: bool = False,
) -> MultiDayResult:
    """Run a continuous multi-day MPC simulation with periodic compliance checkpoints.

    When ``fast_physics=True``, uses the lightweight FastPhysicsEngine.
    """
    from .physics import PhysicsEngine, default_ambient_schedule
    from .state_planner import MPCConfig, MPCPlanner, goals_from_dicts

    # ── Build MPC config & planner ────────────────────────────────────
    cfg_kwargs = dict(mpc_config) if mpc_config else {}
    cfg = MPCConfig(**cfg_kwargs)
    goal_specs = goals_from_dicts(goals, strategy=profile.get("strategy", "balanced"))
    planner = MPCPlanner(config=cfg, goals=goal_specs, actuator_specs=actuator_specs)
    actuator_names = planner.actuator_names

    # ── Resolve ambient schedule ──────────────────────────────────────
    if ambient_schedule is not _UNSET:
        schedule = ambient_schedule
    elif use_ambient_schedule:
        schedule = default_ambient_schedule
    else:
        schedule = None

    # ── Setup environment ─────────────────────────────────────────────
    if fast_physics:
        from .fast_physics import FastPhysicsEngine, FastSubstrateConfig
        from .room_config import _substrate_to_fast

        fast_sub = FastSubstrateConfig()
        if substrate_config is not None:
            fast_sub = _substrate_to_fast(substrate_config)

        fast_kw: dict[str, Any] = dict(
            noise=False,
            ambient_temp=ambient_temp,
            ambient_schedule=schedule,
            actuator_specs=actuator_specs,
            ventilation_fn=ventilation_fn,
            substrate=fast_sub,
        )
        if space is not None:
            fast_kw["tent"] = space
        if plant is not None:
            fast_kw["plant"] = plant
        if initial_conditions:
            fast_kw["temperature"] = initial_conditions.get("temperature", 24.0)
            fast_kw["humidity"] = initial_conditions.get("humidity", 55.0)
            fast_kw["co2"] = initial_conditions.get("co2", 420.0)
            soil = initial_conditions.get("soil_moisture", [38.0])
            fast_kw["vwc"] = sum(soil) / len(soil) if soil else 38.0
        env = FastPhysicsEngine(**fast_kw)
    else:
        env_kwargs: dict[str, Any] = dict(
            noise=False,
            ambient_temp=ambient_temp,
            ambient_schedule=schedule,
            actuator_specs=actuator_specs,
            ventilation_fn=ventilation_fn,
            substrate_fn=substrate_fn,
            substrate_config=substrate_config,
            substrate_container=substrate_container,
        )
        if space is not None:
            env_kwargs["tent"] = space
        if plant is not None:
            env_kwargs["plant"] = plant
        env = PhysicsEngine(**env_kwargs)

        if initial_conditions:
            for key, val in initial_conditions.items():
                if hasattr(env, key):
                    setattr(env, key, val)

    total_steps = (total_duration_minutes * 60) // step_seconds
    checkpoint_steps = (checkpoint_interval_minutes * 60) // step_seconds
    step_hours_inc = step_seconds / 3600.0

    # ── Result containers ─────────────────────────────────────────────
    initial_readings = env.get_readings()
    all_timestamps: list[float] = []
    all_readings: dict[str, list[float]] = {k: [] for k in initial_readings}
    all_intensities: dict[str, list[float]] = {n: [] for n in actuator_names}

    all_solve_times: list[float] = []
    all_costs: list[float] = []
    all_goal_costs: list[float] = []
    all_energy_costs: list[float] = []
    all_rate_costs: list[float] = []

    energy_accum: dict[str, float] = {n: 0.0 for n in actuator_names}
    total_energy_wh: float = 0.0

    phases: list[PhaseResult] = []
    phase_start_idx = 0
    phase_energy_wh = 0.0
    current_phase = 0
    current_hour = float(start_hour)

    logger.info(
        f"=== Multi-day MPC simulation: {total_duration_minutes}min "
        f"({total_duration_minutes / 60:.0f}h), "
        f"checkpoints every {checkpoint_interval_minutes}min "
        f"({checkpoint_interval_minutes / 60:.0f}h), "
        f"horizon={cfg.horizon_minutes}min, "
        f"w_goal={cfg.w_goal}, w_energy={cfg.w_energy}, w_rate={cfg.w_rate} ==="
    )

    # ── Main Loop ────────────────────────────────────────────────────
    solve_every = max(1, cfg.steps_per_interval)  # re-solve once per control interval
    mpc_result = None

    for step_i in range(total_steps):
        elapsed_minutes = step_i * step_seconds / 60.0

        # 1. Step physics
        env.step(step_seconds, current_hour=current_hour)

        # 2. MPC solve (only at control interval boundaries)
        if step_i % solve_every == 0:
            mpc_result = planner.solve(env, current_hour)

        # 3. Apply optimal intensities
        for name, val in mpc_result.optimal_intensities.items():
            setattr(env, name, val)

        # 4. Record readings
        readings = env.get_readings()
        all_timestamps.append(elapsed_minutes)
        for key, val in readings.items():
            all_readings[key].append(val)

        # 5. Record intensities
        for name in actuator_names:
            all_intensities[name].append(
                mpc_result.optimal_intensities.get(name, 0.0)
            )

        # 6. Record MPC diagnostics
        all_solve_times.append(mpc_result.solve_time_ms)
        all_costs.append(mpc_result.cost)
        all_goal_costs.append(mpc_result.goal_cost)
        all_energy_costs.append(mpc_result.energy_cost)
        all_rate_costs.append(mpc_result.rate_cost)

        # 7. Energy tracking
        power_w = env.get_power_watts()
        energy_step_wh = power_w * step_seconds / 3600.0
        total_energy_wh += energy_step_wh
        phase_energy_wh += energy_step_wh

        breakdown = env.get_power_breakdown()
        for name, watts in breakdown.items():
            energy_accum[name] = energy_accum.get(name, 0.0) + watts * step_seconds / 3600.0

        # Advance hour
        current_hour = (current_hour + step_hours_inc) % 24.0

        # ── Checkpoint? ──────────────────────────────────────────────
        steps_into_phase = step_i - (current_phase * checkpoint_steps)
        is_checkpoint = (step_i > 0 and steps_into_phase == checkpoint_steps - 1)
        is_last_step = (step_i == total_steps - 1)

        if is_checkpoint or is_last_step:
            end_idx = len(all_timestamps)
            phase_compliance = _compute_goal_compliance(
                all_readings, goals, float(start_hour), step_seconds,
                start_idx=phase_start_idx, end_idx=end_idx,
            )
            avg_comp = (
                sum(phase_compliance.values()) / len(phase_compliance)
                if phase_compliance else 0.0
            )

            # MPC diagnostics for this phase
            phase_solve_times = all_solve_times[phase_start_idx:end_idx]
            phase_costs = all_costs[phase_start_idx:end_idx]
            avg_solve = (
                sum(phase_solve_times) / len(phase_solve_times)
                if phase_solve_times else 0.0
            )
            avg_cost = (
                sum(phase_costs) / len(phase_costs)
                if phase_costs else 0.0
            )

            phase = PhaseResult(
                phase_num=current_phase,
                start_minutes=phase_start_idx * step_seconds / 60.0,
                end_minutes=elapsed_minutes,
                compliance=phase_compliance,
                avg_compliance=round(avg_comp, 4),
                energy_wh=round(phase_energy_wh, 2),
                avg_solve_time_ms=round(avg_solve, 2),
                avg_cost=round(avg_cost, 4),
            )
            phases.append(phase)

            logger.info(
                f"Phase {current_phase} complete "
                f"({phase.start_minutes:.0f}-{elapsed_minutes:.0f}min): "
                f"compliance={avg_comp:.1%}, energy={phase_energy_wh:.0f}Wh, "
                f"avg solve={avg_solve:.1f}ms"
            )

            # Reset for next phase
            phase_start_idx = end_idx
            phase_energy_wh = 0.0
            current_phase += 1

    # ── Post-processing ───────────────────────────────────────────────
    overall_compliance = _compute_goal_compliance(
        all_readings, goals, float(start_hour), step_seconds,
    )
    effectiveness_trajectory = [p.avg_compliance for p in phases]
    overall = (
        effectiveness_trajectory[-1] - effectiveness_trajectory[0]
        if len(effectiveness_trajectory) >= 2 else 0.0
    )

    logger.info(
        f"Multi-day MPC complete: {len(phases)} phases, "
        f"avg solve={sum(all_solve_times)/max(len(all_solve_times),1):.1f}ms, "
        f"energy={total_energy_wh:.0f}Wh"
    )

    return MultiDayResult(
        total_duration_minutes=total_duration_minutes,
        num_checkpoints=len(phases),
        phases=phases,
        timestamps=all_timestamps,
        readings=all_readings,
        intensities=all_intensities,
        solve_times_ms=all_solve_times,
        costs=all_costs,
        goal_costs=all_goal_costs,
        energy_costs=all_energy_costs,
        rate_costs=all_rate_costs,
        total_energy_wh=round(total_energy_wh, 2),
        energy_breakdown={k: round(v, 2) for k, v in energy_accum.items()},
        compliance=overall_compliance,
        effectiveness_trajectory=effectiveness_trajectory,
        overall_improvement=round(overall, 4),
    )


def run_mpc(
    goals: list[dict],
    profile: dict,
    mpc_config: dict | None = None,
    duration_minutes: int = 180,
    step_seconds: int = 30,
    start_hour: int = 8,
    use_ambient_schedule: bool = True,
    actuator_specs: dict | None = None,
    ventilation_fn: Callable[[float], float] | None = None,
    substrate_fn: Callable | None = None,
    substrate_config: Any = None,
    substrate_container: Any = None,
    space: SpaceConfig | None = None,
    plant: PlantModel | None = None,
    ambient_temp: float = 30.0,
    ambient_schedule: Any = _UNSET,
    initial_conditions: dict | None = None,
    fast_physics: bool = False,
) -> MPCSimulationResult:
    """Run an MPC-controlled simulation.

    The MPC planner optimises actuator intensities (0.0–1.0) over a
    rolling horizon using the physics engine as the prediction model.

    When ``fast_physics=True``, uses the lightweight FastPhysicsEngine
    (10-15x faster) instead of the full PhysicsEngine for MPC rollouts.
    """
    from .physics import PhysicsEngine, default_ambient_schedule
    from .state_planner import MPCConfig, MPCPlanner, goals_from_dicts

    # ── Build config ──────────────────────────────────────────────────
    cfg_kwargs = dict(mpc_config) if mpc_config else {}
    cfg = MPCConfig(**cfg_kwargs)

    goal_specs = goals_from_dicts(goals, strategy=profile.get("strategy", "balanced"))
    planner = MPCPlanner(config=cfg, goals=goal_specs, actuator_specs=actuator_specs)
    actuator_names = planner.actuator_names

    # ── Resolve ambient schedule ──────────────────────────────────────
    if ambient_schedule is not _UNSET:
        _schedule = ambient_schedule
    elif use_ambient_schedule:
        _schedule = default_ambient_schedule
    else:
        _schedule = None

    # ── Setup environment ─────────────────────────────────────────────
    if fast_physics:
        from .fast_physics import FastPhysicsEngine, FastSubstrateConfig

        fast_sub = FastSubstrateConfig()
        if substrate_config is not None:
            from .room_config import _substrate_to_fast
            fast_sub = _substrate_to_fast(substrate_config)

        fast_kw: dict[str, Any] = dict(
            noise=False,
            ambient_temp=ambient_temp,
            ambient_schedule=_schedule,
            actuator_specs=actuator_specs,
            ventilation_fn=ventilation_fn,
            substrate=fast_sub,
        )
        if space is not None:
            fast_kw["tent"] = space
        if plant is not None:
            fast_kw["plant"] = plant
        if initial_conditions:
            fast_kw["temperature"] = initial_conditions.get("temperature", 24.0)
            fast_kw["humidity"] = initial_conditions.get("humidity", 55.0)
            fast_kw["co2"] = initial_conditions.get("co2", 420.0)
            soil = initial_conditions.get("soil_moisture", [38.0])
            fast_kw["vwc"] = sum(soil) / len(soil) if soil else 38.0
        env = FastPhysicsEngine(**fast_kw)
    else:
        env_kwargs: dict[str, Any] = dict(
            noise=False,
            ambient_temp=ambient_temp,
            ambient_schedule=_schedule,
            actuator_specs=actuator_specs,
            ventilation_fn=ventilation_fn,
            substrate_fn=substrate_fn,
            substrate_config=substrate_config,
            substrate_container=substrate_container,
        )
        if space is not None:
            env_kwargs["tent"] = space
        if plant is not None:
            env_kwargs["plant"] = plant
        env = PhysicsEngine(**env_kwargs)

        if initial_conditions:
            for key, val in initial_conditions.items():
                if hasattr(env, key):
                    setattr(env, key, val)

    total_steps = (duration_minutes * 60) // step_seconds

    # Result containers
    result = MPCSimulationResult(duration_minutes=duration_minutes)
    initial_readings = env.get_readings()
    for key in initial_readings:
        result.readings[key] = []
    for name in actuator_names:
        result.intensities[name] = []

    # Energy breakdown accumulators
    energy_accum: dict[str, float] = {n: 0.0 for n in actuator_names}

    # Health scoring
    try:
        from src.services.ecosystem_health import compute_health
        _health_fn = compute_health
    except ImportError:
        _health_fn = None

    step_hours_inc = step_seconds / 3600.0

    logger.info(
        f"MPC simulation: {duration_minutes}min, horizon={cfg.horizon_minutes}min, "
        f"intervals={cfg.num_control_intervals}, "
        f"w_goal={cfg.w_goal}, w_energy={cfg.w_energy}, w_rate={cfg.w_rate}"
    )

    current_hour = float(start_hour)
    solve_every = max(1, cfg.steps_per_interval)  # re-solve once per control interval
    mpc_result = None

    for step_i in range(total_steps):
        elapsed_minutes = step_i * step_seconds / 60.0

        # 1. Step physics
        env.step(step_seconds, current_hour=current_hour)

        # 2. MPC solve (only at control interval boundaries)
        if step_i % solve_every == 0:
            mpc_result = planner.solve(env, current_hour)

        # 3. Apply optimal intensities
        for name, val in mpc_result.optimal_intensities.items():
            setattr(env, name, val)

        # 4. Record readings
        readings = env.get_readings()
        result.timestamps.append(elapsed_minutes)
        for key, val in readings.items():
            result.readings[key].append(val)

        # 5. Record intensities
        for name in actuator_names:
            result.intensities[name].append(
                mpc_result.optimal_intensities.get(name, 0.0)
            )

        # 6. Record MPC diagnostics
        result.solve_times_ms.append(mpc_result.solve_time_ms)
        result.costs.append(mpc_result.cost)
        result.goal_costs.append(mpc_result.goal_cost)
        result.energy_costs.append(mpc_result.energy_cost)
        result.rate_costs.append(mpc_result.rate_cost)

        # 7. Energy tracking
        power_w = env.get_power_watts()
        energy_step_wh = power_w * step_seconds / 3600.0
        result.total_energy_wh += energy_step_wh

        breakdown = env.get_power_breakdown()
        for name, watts in breakdown.items():
            energy_accum[name] = energy_accum.get(name, 0.0) + watts * step_seconds / 3600.0

        # 8. Health scoring
        if _health_fn:
            try:
                health = _health_fn(
                    profile=profile,
                    goals=goals,
                    readings=readings,
                )
                result.health_scores.append((elapsed_minutes, health.score))
            except Exception:
                pass

        # Advance hour
        current_hour = (current_hour + step_hours_inc) % 24.0

    # ── Post-processing ───────────────────────────────────────────────
    result.energy_breakdown = {k: round(v, 2) for k, v in energy_accum.items()}
    result.total_energy_wh = round(result.total_energy_wh, 2)

    # Compliance: % of time each goal metric was in range (day/night aware)
    result.compliance = _compute_goal_compliance(
        result.readings, goals, float(start_hour), step_seconds,
    )

    # Average health
    if result.health_scores:
        result.avg_health = round(
            sum(h for _, h in result.health_scores) / len(result.health_scores), 4
        )

    logger.info(
        f"MPC complete: avg solve={sum(result.solve_times_ms)/len(result.solve_times_ms):.1f}ms, "
        f"avg health={result.avg_health:.1%}, "
        f"energy={result.total_energy_wh:.0f}Wh"
    )

    return result
