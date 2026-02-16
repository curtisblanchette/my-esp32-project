# Grow Tent E2E Simulation Plan

## Context

Build an end-to-end simulation that proves system intelligence by modeling a grow tent with correlated physical variables. Actuator operations (fan, exhaust, irrigation, etc.) influence sensor readings over time, and the real `DecisionEngine` drives actuator commands via rules. The output is a matplotlib timeseries chart showing how all variables interplay.

## Architecture

```
GrowTentEnvironment (physics model)
        ↓ readings
SimulationRunner (time-stepping loop)
        ↓ TelemetryMessage
DecisionEngine.evaluate(telemetry, context)
        ↓ Command[]
SimulationRunner applies commands → flips actuators
        ↓ actuator state changes
GrowTentEnvironment.step() (physics responds)
        ↓ all snapshots
plot_simulation() → matplotlib PNG
```

## New Files

### `apps/cortex/simulations/__init__.py`
Empty init.

### `apps/cortex/simulations/environment.py` — Physics Engine

**`GrowTentEnvironment` class**

State variables:
| Variable | Initial | Range | Unit |
|----------|---------|-------|------|
| `temperature` | 24.0 | 15–45 | °C |
| `humidity` | 55.0 | 20–95 | % |
| `soil_moisture[0..3]` | 70, 68, 72, 65 | 0–100 | % |
| `light_intensity` | 0.0 | 0–1000 | lux |

Actuator states (all `bool`, initially `False`):
`fan`, `exhaust_fan`, `humidifier`, `dehumidifier`, `irrigation`, `light`

**`step(dt_seconds=30)` — advance simulation by one timestep:**

1. **Natural drift** (per step, scaled by `dt_seconds/60`):
   - Temperature: drift toward ambient (30°C day / 20°C night) at rate 0.02°C/min
   - Humidity: drift toward 50% at rate 0.1%/min
   - Soil moisture: natural dry-down at rate 0.05%/min per pot (varied slightly per pot)
   - Light: 0 (no natural light — grow tent is sealed)

2. **Actuator effects** (per step when ON, scaled by `dt/60`):
   | Actuator | Effect |
   |----------|--------|
   | `fan` | temp −0.1°C/min (mild circulation cooling) |
   | `exhaust_fan` | temp −0.3°C/min, humidity −0.5%/min |
   | `humidifier` | humidity +0.8%/min |
   | `dehumidifier` | humidity −0.6%/min |
   | `irrigation` | soil_moisture[i] +2.0%/min, humidity +0.3%/min |
   | `light` | temp +0.15°C/min, light_intensity = 800 lux (off = 0) |

3. **Cross-variable correlations** (scaled by `dt/60`):
   - Higher temp accelerates soil dry-down: `soil_moisture[i] -= (temp - 24) * 0.02`
   - Lower humidity accelerates soil dry-down: `soil_moisture[i] -= (50 - humidity) * 0.01` (clamped ≥ 0)
   - Wet soil raises humidity: `humidity += avg(soil_moisture) * 0.003` when soil > 60%

4. **Noise**: Gaussian per reading (temp ±0.15°C, humidity ±0.3%, soil ±0.5%, light ±5 lux)

5. **Clamp** all values to valid ranges

**`get_readings()` → `dict[str, float]`**: Returns `{temp1, hum1, soil1, soil2, soil3, soil4, light1}`

**`set_actuator(target_id, value)`**: Maps actuator IDs (`relay1`..`relay6`) to named actuator states

### `apps/cortex/simulations/runner.py` — Simulation Harness

**`SimulationRunner` class**

Constructor params:
- `environment: GrowTentEnvironment`
- `rules: list[dict]` — rule dicts to load into DecisionEngine
- `duration_minutes: int = 180` (3 hours default)
- `step_seconds: int = 30` (telemetry every 30s, matching real device rate)

**`run()` method — main loop:**

```
For each timestep:
  1. env.step(dt_seconds)
  2. Build TelemetryMessage from env.get_readings()
  3. Store reading in mock_redis (for DataReader)
  4. Build context: trends (from last 30 min of readings), baselines, forecasts
  5. commands = engine.evaluate(telemetry, context, coordinator=None)
  6. For each command: env.set_actuator(cmd.target, cmd.value)
  7. Record snapshot: {time, readings, actuator_states, commands_fired}
```

Returns `SimulationResult` with:
- `timestamps: list[float]`
- `readings: dict[str, list[float]]` — per-sensor timeseries
- `actuators: dict[str, list[bool]]` — per-actuator state timeseries
- `events: list[dict]` — commands that fired with timestamps and reasons

**Context building** uses real `analysis.build_trend_context()` and `forecaster.linear_forecast()` from the last 30 minutes of accumulated readings. Baselines start empty and accumulate via `CortexMemory.update_baseline()` each step.

### `apps/cortex/simulations/charts.py` — Visualization

**`plot_simulation(result, output_path)` → saves PNG**

Layout: 4 vertically-stacked subplots sharing x-axis (time):

| Panel | Lines | Actuator overlays |
|-------|-------|--------------------|
| **Temperature** | temp1 (°C) | Fan (blue shade), Exhaust (red shade), Light (yellow shade) |
| **Humidity** | hum1 (%) | Humidifier (cyan shade), Dehumidifier (orange shade), Exhaust (red shade) |
| **Soil Moisture** | soil1–soil4 (%, 4 lines) | Irrigation (green shade) |
| **Light** | light1 (lux) | Light switch (yellow shade) |

- Actuator ON periods as semi-transparent `axvspan` shaded regions
- Each actuator gets a distinct color with legend
- X-axis: elapsed minutes (0 → duration)
- Title: "Grow Tent Simulation — {duration}min, {num_rules} rules"
- Figure size: 16×14, dpi 150

### `apps/cortex/simulations/scenarios/grow_tent_rules.py` — Rule Definitions

Defines the rule set as Python dicts (same format as SQLite/YAML):

```python
GROW_TENT_RULES = [
    # Temperature control
    {"name": "high_temp_exhaust_on", ...condition: temp1 > 28 for 60s → exhaust_fan ON},
    {"name": "temp_restored_exhaust_off", ...condition: temp1 < 25 for 60s → exhaust_fan OFF},
    {"name": "mild_heat_fan_on", ...condition: temp1 > 26 for 30s → fan ON},
    {"name": "mild_heat_fan_off", ...condition: temp1 < 25 for 30s → fan OFF},

    # Humidity control
    {"name": "high_humidity_dehumidifier_on", ...condition: hum1 > 65 for 60s → dehumidifier ON},
    {"name": "humidity_restored_dehumidifier_off", ...condition: hum1 < 55 for 60s → dehumidifier OFF},
    {"name": "low_humidity_humidifier_on", ...condition: hum1 < 40 for 60s → humidifier ON},
    {"name": "humidity_ok_humidifier_off", ...condition: hum1 > 50 for 60s → humidifier OFF},

    # Soil moisture / irrigation
    {"name": "dry_soil_irrigate", ...condition: soil1 < 40 for 120s → irrigation ON},
    {"name": "soil_saturated_stop", ...condition: soil1 > 75 for 60s → irrigation OFF},

    # Lighting (time-of-day)
    {"name": "lights_on_morning", ...condition: light1 < 100, time_of_day after 06:00 before 22:00 → light ON},
    {"name": "lights_off_night", ...condition: light1 > 100, time_of_day after 22:00 before 06:00 → light OFF},
]
```

### `apps/cortex/tests/test_simulation_grow_tent.py` — Tests

| Test | Validates |
|------|-----------|
| `test_environment_step_natural_drift` | Soil dries, temp drifts toward ambient without actuators |
| `test_fan_cools_temperature` | Fan ON reduces temp over N steps |
| `test_exhaust_cools_and_dehumidifies` | Exhaust ON reduces both temp and humidity |
| `test_irrigation_raises_soil_and_humidity` | Irrigation ON raises soil moisture + humidity |
| `test_cross_variable_temp_dries_soil` | Higher temp accelerates soil dry-down |
| `test_full_simulation_produces_commands` | Full run with rules produces expected command sequence |
| `test_full_simulation_chart_output` | Chart PNG is generated at expected path |
| `test_actuator_feedback_loop` | Rule fires → actuator ON → reading changes → restore rule fires |

## Dependencies

Add `matplotlib>=3.8` to `apps/cortex/requirements.txt` (dev/simulation dependency).

## Running

```bash
# As a standalone script
cd apps/cortex && python -m simulations.grow_tent

# As a test
cd apps/cortex && pytest tests/test_simulation_grow_tent.py -v

# Output: apps/cortex/simulations/output/grow_tent_simulation.png
```

## Verification

1. Run `pytest tests/ -m "not e2e"` — existing tests pass (no regressions)
2. Run `pytest tests/test_simulation_grow_tent.py -v` — all simulation tests pass
3. Inspect PNG output — chart shows:
   - Temperature oscillating around setpoint as exhaust/fan cycle
   - Humidity controlled by humidifier/dehumidifier with visible correlation to irrigation
   - Soil moisture sawtooth pattern (dry-down → irrigation trigger → re-saturation)
   - Light following on/off schedule with visible temp bump when lights are on
   - Actuator shaded regions aligned with the sensor responses they cause