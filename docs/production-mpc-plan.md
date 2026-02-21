# Production MPC Controller Plan

Bring the MPC state planner into production Cortex, replacing the rules-based `DecisionEngine`. The simulation `PhysicsEngine` and `MPCPlanner` are imported directly — one codebase, no wrappers, no feature flags. Advances to the physics model in `simulations/` are immediately available in production.

## Problem

MPC currently lives in `simulations/` only. Production Cortex still uses the threshold-based `DecisionEngine`. The gaps:

| Component | Simulation | Production |
|-----------|-----------|------------|
| Actuator control | Variable intensity (0.0–1.0) | Binary only (`dict[str, bool]`) |
| Goals | Day/night `timeWindow` | `cortex_goals` has no time window columns |
| Health scoring | Uses flat goal ranges | `ecosystem_health.py` — no day/night filtering |
| Controller | MPCPlanner (SLSQP optimizer) | DecisionEngine (threshold rules) |
| ESP32 firmware | N/A | SWITCH class only (no PWM) |

## Design Principle: One Physics Engine, Two Entry Points

```
simulations/
├── physics.py          ◄── THE physics model (shared)
├── state_planner.py    ◄── THE MPC planner (shared)
├── runner.py           ◄── Simulation entry point (offline, multi-day)
├── room_config.py      ◄── Room geometry loader (shared)
└── ...

src/services/
├── mpc_controller.py   ◄── Production entry point (live telemetry)
│   imports: PhysicsEngine, MPCPlanner, load_room_config
└── ...
```

Production imports `PhysicsEngine` and `MPCPlanner` directly from `simulations/`. No copying, no wrapping. When the physics model gains a new ODE term or the planner gets a better cost function, production gets it immediately.

The `DecisionEngine` (rules) is removed from the telemetry loop. Rules code stays in the repo for reference but is no longer wired into the orchestrator.

---

## Phase 1: Database Schema for Day/Night Goals

Add time window support to the goals table so day/night target ranges can be stored and queried.

### `apps/cortex/src/services/sqlite_client.py`

Add two nullable columns to `cortex_goals`:

```sql
ALTER TABLE cortex_goals ADD COLUMN time_window_on_hour REAL;
ALTER TABLE cortex_goals ADD COLUMN time_window_off_hour REAL;
```

Migration adds nullable columns — backward compatible (NULL means "always active").

Update `create_goal()`, `update_goal()`, `get_goals()` to read/write time window fields. The `timeWindow` JSON shape matches the simulation format:

```json
{
  "metric": "temp",
  "rangeMin": 20.0,
  "rangeMax": 26.0,
  "timeWindow": { "onHour": 6, "offHour": 22 }
}
```

### `apps/cortex/src/api/cortex.py`

Update goal CRUD endpoints to accept/return `timeWindow` in request/response JSON. Map to/from the two database columns.

### Tests

`tests/test_grow_profiles.py`:
- Round-trip: create goal with `timeWindow`, read back, verify `onHour`/`offHour`
- Null time window: create goal without `timeWindow`, verify it reads as always-active
- Update: set time window on existing goal, clear time window

---

## Phase 2: Ecosystem Health Day/Night Scoring

Update health scoring to respect day/night goal windows, so metrics are only scored against goals that are currently active.

### `apps/cortex/src/services/ecosystem_health.py`

Update `compute_health()` to accept a `current_hour: float` parameter. Before scoring, filter goals by time window — only include goals where `current_hour` falls within the `onHour`/`offHour` window.

Extract `_hour_in_window()` from `simulations/state_planner.py` into a shared utility (or import directly) to avoid duplication:

```python
def _hour_in_window(hour: float, on_hour: float, off_hour: float) -> bool:
    if on_hour <= off_hour:
        return on_hour <= hour < off_hour
    return hour >= on_hour or hour < off_hour
```

Goals with no time window (NULL) are always active.

### Tests

`tests/test_ecosystem_health.py`:
- Day goal scored during day, skipped at night
- Night goal scored at night, skipped during day
- Always-active goal (no time window) scored at all hours
- Overall health reflects only active goals

---

## Phase 3: Production MPC Controller Service

Create the production controller that bridges live MQTT telemetry to the simulation planner.

### `apps/cortex/src/services/mpc_controller.py` (new)

```python
from simulations.physics import PhysicsEngine
from simulations.state_planner import MPCPlanner
from simulations.room_config import load_room_config

class MPCController:
    """Production MPC controller.

    Imports PhysicsEngine and MPCPlanner directly from simulations/ —
    same code runs offline sims and live production control.
    """

    def __init__(self, room_config_path, goals, mqtt_client):
        room = load_room_config(room_config_path)
        self.physics = PhysicsEngine(room)
        self.planner = MPCPlanner(physics=self.physics, goals=goals, ...)
        self.mqtt = mqtt_client

    async def on_telemetry(self, device_id, readings):
        """Called on each telemetry message. Syncs model state and re-solves."""
        self.physics.calibrate(readings)  # align model with real sensors
        result = self.planner.solve()
        for actuator, intensity in result.intensities.items():
            self.mqtt.publish_command(device_id, actuator, intensity)
```

Key points:
- **Direct imports** from `simulations.physics` and `simulations.state_planner` — no wrapper layer
- **`calibrate(readings)`** syncs the physics model's internal state with live sensor data each cycle
- **`planner.solve()`** runs the same SLSQP optimizer used in offline simulation
- **Loads goals** from `cortex_goals` (with time windows from Phase 1)
- **Publishes via MQTT** — `Command.value` is `Any`, floats serialize fine via JSON

### `apps/cortex/src/main.py`

Replace `DecisionEngine` with `MPCController` in the `Orchestrator`:

- Load room config from YAML (import `load_room_config` from `simulations/`)
- Load goals from `cortex_goals` with time windows
- `_handle_telemetry()` feeds readings to `mpc_controller.on_telemetry()`
- Remove `DecisionEngine` from the telemetry loop

### Tests

- Unit test: mock `PhysicsEngine` and MQTT client, verify `on_telemetry` produces commands
- Unit test: verify command intensities are within [0.0, 1.0]
- Unit test: verify solve interval throttling
- Integration: mock full pipeline from telemetry → solve → MQTT publish

---

## Phase 4: ESP32 Variable-Intensity Actuators

Add PWM actuator support to the ESP32 firmware so devices can accept float intensity values from MPC.

### `device/actuators.py`

Add `PWM_ACTUATOR` class alongside existing `SWITCH`:

```python
class PWM_ACTUATOR:
    """Variable-intensity actuator controlled via PWM duty cycle."""

    def __init__(self, pin, freq=1000):
        self.pwm = PWM(Pin(pin), freq=freq)

    def set(self, intensity: float):
        """Set intensity 0.0–1.0, mapped to PWM duty 0–1023."""
        duty = int(max(0.0, min(1.0, intensity)) * 1023)
        self.pwm.duty(duty)
```

Applicable actuators:
- EC fans (variable speed)
- Dimmable LEDs (light intensity)
- Proportional valves (flow control)

Binary actuators (humidifier, dehumidifier, irrigation solenoid) keep the `SWITCH` class — MPC quantizes their intensities to 0/1 via `ActuatorSpec.quantize()` before publishing.

### Command handler update

Update the MQTT command handler to detect float values and route to `PWM_ACTUATOR.set()` vs `SWITCH.set()` based on actuator type in the device config.

---

## Phase 5: Integration Wiring

Wire MPC into the full production stack and update the dashboard.

### Telemetry callback loop

- `Orchestrator._handle_telemetry()` calls `mpc_controller.on_telemetry()` directly
- No shadow mode, no fallback — MPC is the controller

### Health scoring

- `EcosystemHealthScorer.compute_health()` passes `current_hour` for day/night goal filtering (Phase 2)
- Health snapshots reflect time-appropriate scoring

### WebSocket broadcasts

- New message type: `{type: "mpc", data: {intensities: {...}, cost: float, health: float}}`
- Sent after each MPC solve so the dashboard can display current control state

### Dashboard updates

- Actuator cards show intensity gauges (0–100%) instead of on/off toggles
- MPC status indicator: last solve time, cost value, horizon length
- Health score reflects day/night goal filtering

---

## Verification Strategy

Each phase includes its own test suite. The overall verification:

```bash
# After each phase — no regressions
cd apps/cortex && pytest tests/ -m "not e2e" -v

# After Phase 3 — simulation still works identically (same code paths)
cd apps/cortex && python -m simulations.simulate --multi-day -v

# After Phase 5 — full production integration
python -m src.main
```

## Dependencies

No new Python packages required — `scipy`, `numpy`, and `copy` are already in `requirements.txt` from the simulation work.
