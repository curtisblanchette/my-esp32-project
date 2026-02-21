# Simulation Framework

Environment simulation for testing the Cortex MPC planner against a physics-based grow environment model.

## CLI Commands

All commands run from `apps/cortex/`:

```bash
# MPC simulation (default) — 3h, 15min horizon, SLSQP, built-in tent
python -m simulations.simulate

# MPC with variable-speed actuators (EC fans, dimmable LEDs)
python -m simulations.simulate --variable -v

# Custom room configuration
python -m simulations.simulate --room simulations/rooms/commercial_10x10.yaml -v

# Custom horizon and energy weight
python -m simulations.simulate --horizon 30 --w-energy 0.1 -v

# Custom duration and start hour
python -m simulations.simulate --duration 360 --start-hour 6

# Strategy override
python -m simulations.simulate --strategy efficiency

# Fast physics (lightweight engine for faster MPC solves)
python -m simulations.simulate --fast-physics -v

# Multi-day simulation — 96h with periodic MPC checkpoints
python -m simulations.simulate --multi-day -v
python -m simulations.simulate --multi-day --fast-physics -v
```

## Tests

```bash
pytest tests/test_simulation_mpc.py tests/test_simulation_multi_day.py tests/test_state_planner.py tests/test_room_config.py tests/test_duct_physics.py tests/test_substrate_physics.py tests/test_fast_physics.py tests/test_fast_physics_mpc.py -v
```

## Architecture Overview

```
MPCPlanner / StatePlanner (scipy.optimize, SLSQP)
  ├── PhysicsEngine (6 state vars, 7+ actuators, plant physiology, HVAC)
  ├── FastPhysicsEngine (4 state vars, simplified ODEs, duck-typed alternative)
  └── Charts (matplotlib visualization)
```

## Physics Engine (`physics.py`)

- **State variables**: temperature, humidity, soil moisture, CO2, leaf temperature, VPD
- **Actuators**: 7+ with configurable control types — `binary` (relay on/off) or `variable` (0-10V/PWM continuous); base set is fan, exhaust_fan, humidifier, dehumidifier, irrigation, light, co2_injector; rooms may add extras (e.g. `hvac` for mini-split AC with COP-based cooling and condensate dehumidification)
- `ActuatorSpec` with optional `humidify_g_per_min` / `dehumidify_g_per_min` for mass-based humidity
- **Psychrometric humidity ODE**: works in absolute humidity (g/m³) internally, converts back to %RH via Tetens equation
  - `rh_to_abs_humidity(temp_c, rh_pct)` and `abs_humidity_to_rh(temp_c, abs_hum)`
  - All sources/sinks in g/min, divided by room volume → g/(m³·min)
  - Key insight: 1 g/m³ at 25°C → +4.3%RH, at 35°C → only +2.5%RH (Clausius-Clapeyron)
- **Plant physiology**: Ball-Berry stomatal conductance, photosynthesis response surface, transpiration with water stress modulation
- **Ventilation**: optional `ventilation_fn` for duct-physics air exchange (falls back to linear ACH model)
- **Substrate**: optional `substrate_fn`/`substrate_config`/`substrate_container` for physics-based soil moisture
- **Energy tracking** and day/night **ambient schedule** (`default_ambient_schedule()`)

## Fast Physics Engine (`fast_physics.py`)

Lightweight drop-in alternative to `PhysicsEngine` for MPC rollouts. Same public interface (duck-typed) with simplified internals.

- **State variables**: temperature, absolute humidity (g/m³), CO2, single average VWC — 4 coupled ODEs vs 6+ in full engine
- **SVP lookup table**: Pre-computed Tetens saturation vapor pressure at integer temps 10-50°C with linear interpolation (eliminates `exp()` calls)
- **Internal absolute humidity**: Stores humidity as g/m³, converts to %RH only in `get_readings()` — eliminates repeated RH↔AH conversions
- **Simplified transpiration**: `k_transp × VPD × LAI × light_frac × vwc_available` — no Ball-Berry stomatal conductance
- **Flat tuple state**: `save_state()` returns 18 floats (no nested lists, no deepcopy needed)
- **FastSubstrateConfig**: Frozen dataclass with `k_dry`, `wilting_vwc`, `stress_onset_vwc`, `irrig_rate`, `sat_vwc`
- `RoomConfig.build_fast_engine()` constructs from room config with automatic substrate mapping

## Duct Physics (`duct_physics.py`)

Pure functions, no classes. YAML inputs in imperial (CFM, inWC, inches, feet), internal math in SI (m³/s, Pa, m).

- **Fan curve**: quadratic `ΔP_fan = P_max × speed² × (1 - (Q/(Q_max×speed))²)`
- **System resistance**: Darcy-Weisbach `ΔP_sys = K × Q²`, K from duct diameter/length/material/elbows + carbon filter
- **Operating point**: analytical solve `Q = Q_eff × √(P_eff / (P_eff + K × Q_eff²))`
- `build_ventilation_fn()` pre-computes system resistance K, returns closure `fan_intensity → ACH`

## Substrate Physics (`substrate_physics.py`)

Pure functions + frozen dataclasses, same pattern as duct_physics.

- **Presets**: `rockwool` (fast dry, covered slab), `coco_perlite_70_30` (moderate), `living_soil` (slow, exposed bed)
- **Config**: `SubstrateConfig` (saturation_vwc, field_capacity_vwc, wilting_point_vwc, stress_onset_vwc, k_dry, infiltration_rate, drainage_rate, surface_evap_factor)
- **Geometry**: `ContainerGeometry` (volume_liters, surface_area_m2, sa_to_vol_ratio property)
- **Dry-back ODE**: `dM/dt = -k_dry × evap_mod × (M - M_wilt)` — exponential, faster when wet, slows near wilting
- **Evap modifier**: Q10=2.0 temperature, VPD-normalized, container SA:V ratio, surface_evap_factor
- **Water stress**: linear ramp between wilting_point and stress_onset, scales transpiration to 0
- `build_substrate_fn()` returns closure `(temp, hum, vwc, irrigation, dt) → (new_vwc, hum_contrib, stress, runoff)`

## Room Configuration (`room_config.py`)

- `RoomConfig` dataclass with `load_room_config(path)` (YAML) and `default_room_config()` (built-in tent)
- Validates space/actuator/ambient/plant/ventilation/substrate sections
- `_parse_ventilation()` builds duct-physics closure, `_parse_substrate()` builds substrate closure
- `default_room_config()` deep-copies `ACTUATOR_SPECS` so mutations don't affect globals
- `--variable` flag overlays onto room config specs (not module globals)

**Preset rooms** in `rooms/`:
- `tent_4x4.yaml` — 1.2m×1.2m, living soil bed, no duct physics
- `commercial_10x10.yaml` — 100m², coco/perlite pots, variable-speed, duct physics
- `warehouse.yaml` — 600m², rockwool slabs, industrial, duct physics

## MPC State Planner (`state_planner.py`)

- Direct nonlinear shooting using state save/restore on `PhysicsEngine` as prediction model
- `scipy.optimize.minimize` (SLSQP) with N×A decision variables (N control intervals × A actuators, dynamic per room)
- Weighted cost function: goal deviation + energy + actuator rate-of-change
- Light/CO2 schedule constraints, warm-start between solves
- Accepts per-room `actuator_specs`; auto-discovers extra actuators (e.g. `hvac`) beyond the 7 defaults
- `MPCConfig.auto_scale()` adjusts horizon/weights for room volume (capped at 4×)
- **Performance**: uses `PhysicsEngine.save_state()`/`restore_state()` for fast rollouts (no deepcopy), adaptive `maxiter` on warm-start
- **FastPhysicsEngine compatible**: duck-typed interface allows `--fast-physics` flag to swap in simplified engine for MPC rollouts

## Scenarios (`scenarios/default.py`)

Exports: `PROFILE`, `GOALS`, `FLOWER_GOALS`, `MPC_CONFIG`, `VARIABLE_OVERRIDES`

## Charts (`charts.py`)

- `plot_mpc_simulation` — actuator intensity heatmap and cost decomposition
- `plot_multi_day` — timeline with effectiveness trajectory and learning phase hatching

## Key Files

| File | Description |
|------|-------------|
| `simulate.py` | CLI entry point with MPC flags |
| `physics.py` | PhysicsEngine: state vars, actuators, plant model, energy |
| `fast_physics.py` | FastPhysicsEngine: simplified ODEs, SVP lookup, flat state |
| `duct_physics.py` | Fan curves, Darcy-Weisbach, ventilation closure |
| `substrate_physics.py` | Substrate presets, dry-back ODE, substrate closure |
| `room_config.py` | RoomConfig dataclass, YAML loader, defaults |
| `rooms/*.yaml` | Preset room configurations |
| `state_planner.py` | MPC planner: SLSQP optimizer, cost functions |
| `runner.py` | MPC runner: time-stepping, multi-day |
| `charts.py` | Matplotlib visualization functions |
| `scenarios/default.py` | Profiles, goals, MPC config |

**Test files** (in `apps/cortex/tests/`):
- `test_simulation_mpc.py` — MPC runner, energy tracking, compliance, chart generation
- `test_simulation_multi_day.py` — ambient schedule, multi-day runner, convergence
- `test_state_planner.py` — MPCConfig, cost functions, solver convergence, warm start
- `test_duct_physics.py` — system resistance, fan operating point, ACH, unit conversions
- `test_substrate_physics.py` — presets, geometry, evap modifier, stress, irrigation
- `test_fast_physics.py` — SVP lookup, psychrometrics, direction tests, save/restore, benchmarks, regression vs PhysicsEngine
- `test_fast_physics_mpc.py` — MPC solve with FastPhysicsEngine, trajectory, cooling/CO2 control, run_mpc integration
- `test_room_config.py` — YAML loading, validation, ventilation/substrate parsing, MPC integration, build_fast_engine
