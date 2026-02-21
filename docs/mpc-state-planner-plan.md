# MPC State Planner — Grow Tent Simulation

## Context

The grow tent simulation has a physics engine with 6 state variables, 7 continuous actuators, and full plant physiology (Ball-Berry stomatal conductance, photosynthesis, transpiration). The current controller is a **reactive rule-based system** — hysteresis rules fire binary ON/OFF commands when thresholds are crossed, one variable at a time. This works but has fundamental limitations:

1. **No multi-step lookahead** — rules react to the present, can't anticipate future states
2. **No cross-variable optimization** — exhaust fan affects temp, humidity, AND CO2 simultaneously, but rules only check one sensor
3. **Binary actuators** — rules fire ON or OFF, but actuators support continuous 0.0–1.0 intensity
4. **No energy awareness** — rules have no concept of power cost
5. **Actuator chattering** — competing rules (fan vs humidifier) can oscillate

An MPC (Model Predictive Control) planner solves all five by optimizing actuator intensities over a rolling horizon using the actual physics engine as its prediction model.

## Architecture

### Control Flow

**Current (reactive):**
```
Readings → Rules Engine → Binary Commands → Environment
```

**MPC (predictive):**
```
Readings → MPC Planner → deepcopy(env) → rollout N steps → scipy.optimize
              ↓                                                    ↓
    Apply first step's optimal intensities (0.0–1.0)    ← optimal trajectory
```

### Key Design Decision: Direct Nonlinear Shooting

Rather than linearizing the physics into matrices (A, B, C, D), the MPC uses the actual `GrowTentEnvironment.step()` as its prediction model via `copy.deepcopy()`. This gives:

- Perfect cross-variable coupling (exhaust affects temp + humidity + CO2)
- Plant physiology included automatically (photosynthesis, transpiration, VPD)
- No model mismatch — the prediction IS the physics engine
- Fast enough: ~0.3ms per rollout evaluation, ~15-30ms total solve time per step

## New Dependency

```
scipy>=1.11.0   # scipy.optimize.minimize (SLSQP)
```

Added to `apps/cortex/requirements.txt`.

## Data Structures

### `MPCConfig`
```python
@dataclass
class MPCConfig:
    horizon_minutes: float = 15.0       # prediction window
    control_interval_s: float = 150.0   # actuator update period within horizon (2.5 min)
    w_goal: float = 1.0                 # goal deviation weight
    w_energy: float = 0.05              # energy cost weight
    w_rate: float = 0.1                 # actuator rate-of-change penalty
    max_iterations: int = 100           # scipy max iterations
    method: str = "SLSQP"              # optimizer method
    light_schedule: dict | None = {"on_hour": 6, "off_hour": 22}
    warm_start: bool = True
```

With 15min horizon at 150s control intervals = **6 intervals × 7 actuators = 42 decision variables**.

### `GoalSpec`
```python
@dataclass
class GoalSpec:
    metric: str         # "temp1", "hum1", "co2_1", "vpd1", "soil1"
    target: float       # midpoint of range
    range_min: float
    range_max: float
    tolerance: float
    weight: float       # priority (from goals dict)
```

### `MPCResult`
```python
@dataclass
class MPCResult:
    optimal_intensities: dict[str, float]  # first-step setpoints
    predicted_trajectory: list[dict[str, float]]
    cost: float
    goal_cost: float
    energy_cost: float
    rate_cost: float
    solve_time_ms: float
    num_iterations: int
    success: bool
```

### `MPCSimulationResult`
```python
@dataclass
class MPCSimulationResult:
    timestamps: list[float]
    readings: dict[str, list[float]]
    intensities: dict[str, list[float]]     # continuous 0.0–1.0 per actuator
    health_scores: list[tuple[float, float]]
    solve_times_ms: list[float]
    costs: list[float]
    goal_costs: list[float]
    energy_costs: list[float]
    rate_costs: list[float]
    total_energy_wh: float
    energy_breakdown: dict[str, float]      # per-actuator Wh
    compliance: dict[str, float]            # % time in goal range per metric
    avg_health: float
    duration_minutes: int
```

## Optimization Problem

### Decision Variables
Flat vector `u` of shape `(N_intervals × 7,)`. Each block of 7 = `[fan, exhaust_fan, humidifier, dehumidifier, irrigation, light, co2_injector]` intensities for one control interval. Bounds: `[0.0, 1.0]` per element.

### Prediction (Rollout)
```python
def _rollout(u, env_copy, current_hour, config):
    for k in range(N_intervals):
        u_k = u[k*7 : (k+1)*7]
        # Set all 7 actuator intensities
        for i, name in enumerate(ACTUATOR_NAMES):
            setattr(env_copy, name, u_k[i])
        # Step physics for control_interval_s
        env_copy.step(config.control_interval_s)
        trajectory.append(env_copy.get_readings())
    return trajectory
```

### Cost Function

```
J(u) = w_goal × J_goal  +  w_energy × J_energy  +  w_rate × J_rate
```

**J_goal** — Quadratic penalty for readings outside goal ranges, averaged over the trajectory:
```python
for each step k, for each goal:
    value = trajectory[k][goal.metric]
    if range_min <= value <= range_max:
        cost = 0  # inside band = free
    else:
        deviation = distance_to_nearest_boundary / range_width
        cost = goal.weight × deviation²
```

**J_energy** — Normalized power consumption:
```python
for each step k:
    power = sum(u_k[i] * max_watts[i] for i in 7 actuators)
    cost += power / max_total_power
cost /= N_intervals
```

**J_rate** — Actuator smoothness penalty (prevents chattering):
```python
prev = current_intensities
for each step k:
    cost += sum((u_k - prev)²)
    prev = u_k
cost /= N_intervals
```

### Constraints
- **Bounds**: `0 ≤ u[i] ≤ 1` for all elements (scipy bounds)
- **Light schedule**: For control intervals during night hours (22:00–06:00), light bounds forced to `(0.0, 0.0)`. CO2 injector bounds also forced to `(0.0, 0.0)` during darkness (no photosynthesis benefit).
- **Horizon spanning transitions**: Each interval's hour is computed; bounds set per-interval.

### Warm Start
Previous solution shifted by one control interval (drop first, duplicate last) used as `x0` for next solve. Reduces iterations significantly for smooth trajectories.

## Implementation Steps

### Step 1: Add scipy dependency
- Add `scipy>=1.11.0` to `apps/cortex/requirements.txt`
- `pip install scipy`

### Step 2: Create `state_planner.py`

**File:** `apps/cortex/simulations/state_planner.py`

Core classes and functions:
- `ACTUATOR_NAMES` — fixed-order list of 7 actuator names (for vectorization)
- `MPCConfig` dataclass
- `GoalSpec` dataclass + `goals_from_dicts(goal_dicts, profile)` converter
- `MPCResult` dataclass
- `MPCPlannerState` — warm start persistent state
- `MPCPlanner` class:
  - `__init__(config, goals, strategy)`
  - `solve(environment, current_hour) → MPCResult`
  - `_rollout(u, env, current_hour) → (cost, trajectory)`
  - `_goal_cost(trajectory, goals) → float`
  - `_energy_cost(u) → float`
  - `_rate_cost(u, current_intensities) → float`
  - `_build_bounds(current_hour) → list[tuple]`
  - `_warm_start_x0(previous_solution) → np.ndarray`

### Step 3: Add MPC scenario config

**File:** `apps/cortex/simulations/scenarios/grow_tent.py`

Add:
```python
MPC_CONFIG = {
    "horizon_minutes": 15.0,
    "control_interval_s": 150.0,
    "w_goal": 1.0,
    "w_energy": 0.05,
    "w_rate": 0.1,
    "method": "SLSQP",
    "light_schedule": {"on_hour": 6, "off_hour": 22},
}
```

### Step 4: Add MPC runner

**File:** `apps/cortex/simulations/runner.py`

Add:
- `MPCSimulationResult` dataclass
- `run_mpc(goals, profile, config, duration_minutes, step_seconds, start_hour, use_ambient_schedule) → MPCSimulationResult` function
- Time-stepping loop: at each step, call `planner.solve()`, apply intensities, record readings/diagnostics
- Track energy: `env.get_power_watts() × dt / 3600` accumulated per step
- Track health: `compute_health()` at each step
- Compute compliance: % time each metric is within goal range

### Step 5: Add CLI flags

**File:** `apps/cortex/simulations/grow_tent.py`

Add arguments:
- `--mpc` — Use MPC state planner instead of rules
- `--compare-mpc` — A/B comparison: rules vs MPC
- `--horizon` — MPC prediction horizon in minutes (default: 15)
- `--w-energy` — Energy cost weight (default: 0.05)
- `--compare-mpc-configs` — Compare two MPC configurations (e.g., different horizons)

Add dispatch functions:
- `_run_mpc(args)` — Run MPC simulation, print report, save chart
- `_run_compare_mpc(args)` — Run rules + MPC side by side, print comparison table
- `_run_compare_mpc_configs(args)` — Run two MPC configs side by side

### Step 6: Add MPC charts

**File:** `apps/cortex/simulations/charts.py`

**`plot_mpc_simulation(result, output_path)`** — Multi-panel chart:
- Row 0: Temperature + Humidity (same as existing, but with continuous intensity shading instead of binary)
- Row 1: CO2 + VPD (with target bands)
- Row 2: Actuator intensity heatmap (7 rows, time on x-axis, color = intensity 0–1)
- Row 3: MPC diagnostics — cost breakdown (goal/energy/rate stacked area) + solve time

**`plot_mpc_comparison(rules_result, mpc_result, output_path)`** — Side-by-side:
- Left column: Rules controller timeseries
- Right column: MPC controller timeseries
- Bottom row: Comparison metrics (compliance %, energy Wh, avg health, commands/transitions)

### Step 7: Write tests

**File:** `apps/cortex/tests/test_state_planner.py`

```
TestMPCConfig:
    test_horizon_steps
    test_num_control_intervals
    test_default_config_valid

TestGoalSpec:
    test_goals_from_dicts
    test_goals_from_dicts_with_strategy

TestMPCCostFunction:
    test_in_range_zero_cost
    test_out_of_range_quadratic
    test_priority_weighting
    test_energy_proportional_to_power
    test_rate_zero_for_constant
    test_rate_increases_with_change

TestMPCRollout:
    test_rollout_advances_state
    test_rollout_preserves_original_env
    test_trajectory_length_matches_intervals
    test_light_schedule_enforced

TestMPCSolver:
    test_returns_valid_intensities_in_bounds
    test_reduces_cost_vs_zero_input
    test_warm_start_fewer_iterations
    test_completes_under_1_second
    test_high_temp_activates_cooling
    test_low_co2_activates_injector
    test_cross_variable_tradeoff

TestLightSchedule:
    test_light_off_during_night
    test_co2_off_during_night
    test_horizon_spanning_day_night_transition
```

**File:** `apps/cortex/tests/test_simulation_mpc.py`

```
TestMPCSimulation:
    test_runs_to_completion
    test_health_improves_over_time
    test_result_contains_diagnostics
    test_energy_tracked
    test_compliance_computed

TestMPCComparison:
    test_comparison_runs_both
    test_comparison_chart_output

TestMPCChart:
    test_mpc_chart_saves_file
    test_comparison_chart_saves_file
```

### Step 8: Run full test suite + simulations

1. `cd apps/cortex && pytest tests/ -m "not e2e" -v` — all tests pass
2. `python -m simulations.grow_tent --mpc -v` — 3h MPC simulation
3. `python -m simulations.grow_tent --compare-mpc -v` — Rules vs MPC comparison
4. `python -m simulations.grow_tent --mpc --multi-day -v` — future extension (not in this phase)

### Step 9: Update docs via `/docs`

## Files Modified

| File | Change |
|------|--------|
| `apps/cortex/requirements.txt` | Add `scipy>=1.11.0` |
| `apps/cortex/simulations/state_planner.py` | **New** — Core MPC planner |
| `apps/cortex/simulations/runner.py` | Add `MPCSimulationResult`, `run_mpc()` |
| `apps/cortex/simulations/grow_tent.py` | Add `--mpc`, `--compare-mpc`, `--horizon`, `--w-energy`, `--compare-mpc-configs` flags |
| `apps/cortex/simulations/charts.py` | Add `plot_mpc_simulation()`, `plot_mpc_comparison()` |
| `apps/cortex/simulations/scenarios/grow_tent.py` | Add `MPC_CONFIG` dict |
| `apps/cortex/tests/test_state_planner.py` | **New** — Unit tests for planner |
| `apps/cortex/tests/test_simulation_mpc.py` | **New** — Integration tests |

## Files NOT Modified

- `environment.py` — used as-is (deepcopy + step)
- `decision_engine.py` — MPC is a separate controller, not a modification
- `ecosystem_health.py` — reused for health scoring inside MPC
- `impact_estimator.py` — MPC subsumes its single-step prediction
- `effect_tracker.py` — not needed (MPC uses physics directly, not learned effects)

## Performance Budget

| Metric | Value |
|--------|-------|
| Decision variables | 42 (6 intervals × 7 actuators) |
| Rollout time | ~0.3ms |
| Optimizer evaluations | ~50-100 (warm-started SLSQP) |
| **Total solve time** | **15-30ms per step** |
| Budget | 1000ms |
| Headroom | **~30-60x** |

## Expected Outcomes

Compared to rule-based hysteresis control, MPC should demonstrate:
- **Higher compliance** — proactive control keeps variables in-range more consistently
- **Less chattering** — rate-of-change penalty smooths actuator transitions
- **Continuous intensity** — partial fan speed, dimmed lights vs binary ON/OFF
- **Cross-variable coordination** — exhaust fan intensity balanced for temp+humidity+CO2 simultaneously
- **Energy savings** — partial intensity uses less power than full-blast cycling
- **Better VPD stability** — temp and humidity co-optimized for VPD target
