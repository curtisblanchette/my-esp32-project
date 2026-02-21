# Actuator Control Types & Naming Cleanup

## Problem

1. **Unrealistic actuator model**: The simulation treats all 7 actuators as continuous
   0.0–1.0. Real commercial facilities use mostly on/off relay control, with variable
   (0-10V / PWM) only on select equipment (EC fans, dimmable LEDs, modulating
   dehumidifiers). This produces unrealistic simulation results.

2. **"Grow tent" naming**: Hardcoded everywhere — class names, file names, device IDs,
   locations. The system is scaling beyond a single tent. Need generic naming.

## Part 1: Naming Cleanup

### Renames

| Before | After | Reason |
|--------|-------|--------|
| `GrowTentEnvironment` class | `PhysicsEngine` | User-confirmed; physics sim, not a facility |
| `environment.py` | `physics.py` | Frees "environment" for the user-facing concept |
| `grow_tent.py` (CLI) | `simulate.py` | Generic entry point |
| `scenarios/grow_tent.py` | `scenarios/default.py` | Not tied to a tent |
| `test_simulation_grow_tent.py` | `test_simulation_physics.py` | Matches renamed module |
| `DEVICE_ID = "sim-grow-tent"` | `DEVICE_ID = "sim-default"` | Generic |
| `LOCATION = "grow-tent"` | `LOCATION = "default"` | Generic |
| `TentConfig` class | `SpaceConfig` | A room, warehouse, tent — any enclosed space |

All other files (runner, charts, state_planner, tests) update their imports
and string references mechanically.

### Migration

- Update CLAUDE.md simulation commands: `python -m simulations.simulate ...`
- Update all test imports
- `scenarios/grow_tent.py` → `scenarios/default.py` (the scenario data stays the same)

## Part 2: Actuator Control Types

### Two control types (binary and variable)

Based on real-world commercial equipment research:

| Control | Signal | Equipment |
|---------|--------|-----------|
| **binary** | Relay on/off | Irrigation solenoids, CO2 solenoids, budget fans, basic dehumidifiers |
| **variable** | 0-10V / PWM continuous | EC fans, dimmable LED drivers, modulating dehumidifiers |

"Stepped" (3-speed switch) is a consumer product thing, not an automation interface.
Dropped from the model.

### `ActuatorSpec` changes

```python
@dataclass
class ActuatorSpec:
    name: str
    relay_id: str
    max_watts: float
    control_type: str = "binary"   # "binary" | "variable"

    def quantize(self, value: float) -> float:
        """Constrain a raw 0.0–1.0 intensity to this actuator's control type."""
        v = max(0.0, min(1.0, float(value)))
        if self.control_type == "binary":
            return 1.0 if v >= 0.5 else 0.0
        return v  # variable — pass through clamped
```

### Default: all binary

Matches the grower's advice — "set them all as on/off to start":

```python
ACTUATOR_SPECS = {
    "relay1": ActuatorSpec("fan",           "relay1", 45,  "binary"),
    "relay2": ActuatorSpec("exhaust_fan",   "relay2", 85,  "binary"),
    "relay3": ActuatorSpec("humidifier",    "relay3", 30,  "binary"),
    "relay4": ActuatorSpec("dehumidifier",  "relay4", 300, "binary"),
    "relay5": ActuatorSpec("irrigation",    "relay5", 15,  "binary"),
    "relay6": ActuatorSpec("light",         "relay6", 480, "binary"),
    "relay7": ActuatorSpec("co2_injector",  "relay7", 10,  "binary"),
}
```

### Scenario overrides for variable-speed facilities

In `scenarios/default.py`:

```python
VARIABLE_OVERRIDES: dict[str, dict] = {
    "relay2": {"control_type": "variable"},   # EC exhaust fan (0-10V)
    "relay6": {"control_type": "variable"},   # dimmable LED driver
}
```

### Enforcement point: `PhysicsEngine.set_actuator()`

Single choke point — all callers (rules engine, MPC, manual) flow through here:

```python
def set_actuator(self, target_id: str, value: bool | float) -> None:
    attr = ACTUATOR_MAP.get(target_id, target_id)
    if not hasattr(self, attr):
        return
    spec = _SPEC_BY_NAME.get(attr)
    if isinstance(value, bool):
        intensity = 1.0 if value else 0.0
    else:
        intensity = float(max(0.0, min(1.0, value)))
    if spec:
        intensity = spec.quantize(intensity)
    setattr(self, attr, intensity)
```

### MPC planner integration

Binary actuators get `{0.0, 1.0}` integer-like bounds so the optimizer knows not
to use intermediate values. Variable actuators keep `[0.0, 1.0]` continuous bounds.
The rollout also quantizes via `set_actuator()` so cost evaluation is realistic.

### CLI changes

```bash
# Default: all binary (realistic baseline)
python -m simulations.simulate --multi-day -v

# Variable-speed equipment on select actuators
python -m simulations.simulate --multi-day --variable -v

# MPC with binary constraints
python -m simulations.simulate --mpc -v

# MPC with variable actuators
python -m simulations.simulate --mpc --variable -v
```

## Files Changed

### Part 1 — Naming (mechanical renames, no logic changes)

| File | Change |
|------|--------|
| `simulations/environment.py` → `simulations/physics.py` | Rename file, `GrowTentEnvironment` → `PhysicsEngine`, `TentConfig` → `SpaceConfig` |
| `simulations/grow_tent.py` → `simulations/simulate.py` | Rename file, update imports |
| `simulations/scenarios/grow_tent.py` → `simulations/scenarios/default.py` | Rename file |
| `simulations/runner.py` | Update imports and string refs |
| `simulations/charts.py` | Update imports |
| `simulations/state_planner.py` | Update imports |
| `tests/test_simulation_grow_tent.py` → `tests/test_simulation_physics.py` | Rename, update imports |
| `tests/test_simulation_*.py` (6 files) | Update imports |
| `tests/test_state_planner.py` | Update imports |
| `tests/test_simulation_mpc.py` | Update imports |
| CLAUDE.md | Update simulation commands |

### Part 2 — Actuator control types (logic changes)

| File | Change |
|------|--------|
| `simulations/physics.py` | Add `control_type` to `ActuatorSpec`, add `quantize()`, enforce in `set_actuator()` |
| `simulations/scenarios/default.py` | Add `VARIABLE_OVERRIDES` dict |
| `simulations/simulate.py` | Add `--variable` CLI flag, apply overrides to specs |
| `simulations/state_planner.py` | MPC bounds respect control types |
| `tests/test_simulation_physics.py` | Tests: quantize binary/variable, enforcement in set_actuator |
| `tests/test_simulation_mpc.py` | Tests: MPC with binary constraints |

## Out of Scope

- Production decision engine changes (stays boolean)
- Device firmware PWM support
- MQTT command protocol changes
- Effect/outcome tracker intensity awareness
