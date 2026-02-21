# Ecosystem Effectiveness — Rethinking What "Effective" Means

## Problem Statement

The current outcome tracker answers: **"Did this command move the target sensor in the desired direction?"**

That's confirming physics. Exhaust fan ON → temperature drops. Irrigation ON → soil moisture rises. This is deterministic cause-and-effect — not intelligence.

But there's a deeper problem: every rule fights independently. The fan fires for temperature, crashes humidity, triggers the humidifier, which raises temperature, which fires the fan again. There is no arbiter deciding whether the net outcome is worth the energy, or whether the system is actually converging toward a healthy state.

What Cortex should answer instead:

1. **"What is this environment trying to achieve?"** — Maintenance mode (stay in range, save energy) vs precision mode (this strain needs VPD at 1.1 kPa during flower week 4, spend whatever it takes)
2. **"Is the ecosystem healthy?"** — Not per-sensor, but as a whole — including derived metrics like VPD that couple multiple sensors
3. **"What are the tradeoffs?"** — When I turn on the fan for temperature, what happens to humidity, soil moisture, and energy usage? Is the net impact positive given the current objective?

---

## What Changes

### Current: Per-Command Scoring (Remove)

```
Command fired → snapshot target sensor → check at 1m/5m/10m → score ±1.0
```

- Measures one sensor per command
- Ignores side effects (fan cools temp AND dries humidity)
- "Effective" means "sensor moved in desired direction" — trivially deterministic
- Rules fight each other with no coordination

### Proposed: Ecosystem-Level Effectiveness

Four new concepts replace per-command scoring:

| Concept | What It Tracks | Granularity |
|---------|---------------|-------------|
| **Grow Profiles** | What the environment is optimizing for — strain, growth phase, strategy | Per-location, user-configured |
| **Goals** | Target ranges for raw sensors + derived metrics (VPD, dry-back rate) | Per-profile phase, time-aware |
| **Ecosystem Health** | Weighted compliance across all goals + energy cost | Rolling window (1h/6h/24h) |
| **Effect Profiles** | Learned multi-sensor impact of each actuator | Per-actuator, incremental |

---

## Phase 0: Harden the Control Layer

Before building ecosystem-level intelligence on top, the underlying control model needs to be sound. Currently the engine is a collection of independent bang-bang controllers that don't know about each other or the actuator state they're controlling.

### What's Wrong Today

| Problem | Current Behavior | Impact |
|---------|-----------------|--------|
| **No relay state awareness** | Engine doesn't know if relay is already ON. Re-issues `relay2=true` every eval cycle (blocked only by cooldown). | Floods of redundant MQTT commands. Simulation works around this (runner.py:224-226) but production doesn't. |
| **Two rules per actuator** | ON rule (`temp > 28`) and OFF rule (`temp < 25`) are completely independent rules. | Rule authoring complexity doubles. ON and OFF rules can conflict at boundary. No single source of truth for "what controls this actuator." |
| **Hardcoded cooldown** | `cooldown_seconds = 60` on every `SensorState`. | Fan motors need longer cooldowns than valves. No way to tune per-rule or per-actuator. |
| **No priority** | If two rules target the same relay, both fire. Last one wins. | Race conditions. A safety rule can be overridden by a comfort rule. |
| **No dead band** | Threshold comparison is exact: `28.01 > 28.0` triggers. | Sensor noise causes flickering at threshold boundaries. Duration timer helps but doesn't eliminate it. |

### 0a. Unified Hysteresis Rules

Replace the two-rule ON/OFF pattern with a single rule that has both thresholds:

```python
@dataclass
class RuleCondition:
    sensor: str
    operator: str               # ">" | "<" | ">=" | "<=" | "==" | "!="
    threshold: float | str       # ON threshold — when to activate
    off_threshold: float | None = None   # OFF threshold — when to deactivate (hysteresis)
    dead_band: float = 0.0       # ignore fluctuations within ±dead_band of threshold
    duration_seconds: int = 0
    cooldown_seconds: float = 60  # per-rule configurable (moved from SensorState)
    # ... existing fields (trend, time_of_day, forecast, etc.)
```

**Example — temperature control becomes one rule:**

```yaml
# Before: TWO rules
- name: high_temp_exhaust_on       # temp > 28 → relay2=true
- name: temp_restored_exhaust_off  # temp < 25 → relay2=false

# After: ONE rule with hysteresis
- name: temp_exhaust_control
  description: "Exhaust fan for temperature regulation"
  condition:
    sensor: temp1
    operator: ">"
    threshold: 28          # turn ON when temp exceeds 28°C
    off_threshold: 25      # turn OFF when temp drops below 25°C
    dead_band: 0.3         # ignore fluctuations within ±0.3°C of either threshold
    duration_seconds: 60
    cooldown_seconds: 300  # fan motor: 5 min between cycles
  action:
    target: relay2
    action: set
    value: true            # value when condition triggers (false when off_threshold triggers)
    reason: "Temperature control — exhaust fan"
```

**Engine behavior:**

```python
# Pseudocode for hysteresis evaluation
if rule has off_threshold:
    relay_is_on = actuator_states.get(rule.action.target, False)

    if not relay_is_on:
        # Check ON condition (with dead_band)
        if value > (threshold + dead_band) for duration → turn ON
    else:
        # Check OFF condition (with dead_band)
        if value < (off_threshold - dead_band) for duration → turn OFF
else:
    # Legacy behavior: single threshold, no hysteresis
    existing_logic()
```

**Key properties:**
- Single rule = single source of truth for actuator control
- `off_threshold` is optional — existing rules work unchanged (backward compatible)
- Dead band applies to both ON and OFF thresholds
- The engine knows the actuator's current state and evaluates the correct branch

### 0b. Relay State Awareness

The engine must know what state each actuator is currently in:

```python
@dataclass
class DecisionEngine:
    rules: list[Rule] = field(default_factory=list)
    sensor_states: dict[str, SensorState] = field(default_factory=dict)
    actuator_states: dict[str, bool] = field(default_factory=dict)  # NEW: relay2 → True/False
```

**Updated from three sources:**
1. Command execution — when the engine fires a command, update `actuator_states`
2. Ack messages — when device confirms state change via MQTT ack
3. Startup — loaded from last known state in SQLite

**Benefits:**
- Hysteresis rules can check "am I already on?" before deciding which threshold to evaluate
- No redundant commands — if relay is already ON, don't issue ON again
- Conflict detection — two rules trying to set the same relay to opposite values in the same cycle

### 0c. Configurable Cooldown

Move `cooldown_seconds` from `SensorState` (hardcoded 60s) to `RuleCondition` (per-rule):

```python
# Before
@dataclass
class SensorState:
    cooldown_seconds: float = 60  # hardcoded for all rules

# After — cooldown is per-rule, defined in condition
@dataclass
class RuleCondition:
    cooldown_seconds: float = 60  # default 60s, configurable per rule
```

**Guidance:**
- **Fans/motors**: 120-300s (prevent rapid cycling, motor wear)
- **Valves/irrigation**: 30-60s (mechanical, but less wear concern)
- **Lights**: 0s (solid state, no cycling concern — but rarely toggled)
- **Humidifier/dehumidifier**: 60-120s

### 0d. Rule Priority

Add explicit priority for conflict resolution when multiple rules target the same actuator:

```python
@dataclass
class Rule:
    name: str
    description: str
    condition: RuleCondition
    action: RuleAction
    enabled: bool = True
    id: str = ""
    priority: int = 0  # NEW: higher = wins conflicts. 0 = normal, 10 = safety override
```

**Conflict resolution in `evaluate()`:**

```python
# After evaluating all rules, resolve conflicts:
# Group commands by (device_id, target)
# If multiple commands for same relay, highest priority wins
# If tied, most recent condition_met_since wins (been true longer = more urgent)
```

**Use case:**
- Safety rule (priority=10): `temp > 35°C → ALL fans ON` — never overridden
- Comfort rule (priority=0): `temp < 25°C → fan OFF` — yields to safety

### 0e. Simulation Proof

Before deploying to production, run A/B comparison in simulation:

**Scenario A (current):** 12 rules (6 ON/OFF pairs), hardcoded 60s cooldown, no relay awareness in engine

**Scenario B (Phase 0):** 6 unified hysteresis rules, per-rule cooldowns, relay state awareness, dead bands

**Metrics to compare:**
- Total commands issued (redundant commands should drop to zero)
- Actuator state changes (fewer transitions = less cycling)
- Time in oscillation (fan ON/OFF within 5min window)
- Energy usage (total actuator-on-time)
- Sensor compliance (% time in target range should stay the same or improve)

```bash
python -m simulations.run --compare-control
# Outputs side-by-side: bang-bang vs hysteresis for same physics scenario
```

### 0f. Migration

**Schema changes:**
- `RuleCondition`: add `off_threshold`, `dead_band`, move `cooldown_seconds` from SensorState
- `Rule`: add `priority`
- `DecisionEngine`: add `actuator_states` dict
- `SensorState`: remove `cooldown_seconds` (moved to RuleCondition)

**Rule migration:**
- Existing ON/OFF rule pairs can be automatically merged into hysteresis rules:
  - Find pairs sharing the same `action.target` with opposite `action.value`
  - ON rule's threshold → `threshold`, OFF rule's threshold → `off_threshold`
  - Take the longer `duration_seconds` from either rule
  - Mark merged rules in migration log
- Standalone rules (no pair) keep working with `off_threshold: null` (legacy behavior)

**Backward compatibility:**
- `off_threshold: null` = existing behavior (no hysteresis)
- `dead_band: 0.0` = existing behavior (no dead band)
- `cooldown_seconds: 60` = existing default
- `priority: 0` = existing behavior (no priority override)

### 0g. Implementation Steps

| Step | Description | Files | Tests |
|------|-------------|-------|-------|
| **0.1** | Add `off_threshold`, `dead_band`, `cooldown_seconds` to `RuleCondition` | `decision_engine.py` | update `test_decision_engine.py` |
| **0.2** | Add `priority` to `Rule` | `decision_engine.py` | update `test_decision_engine.py` |
| **0.3** | Add `actuator_states` to `DecisionEngine`, update on command fire | `decision_engine.py` | new tests |
| **0.4** | Implement hysteresis evaluation (ON/OFF branch based on actuator state) | `decision_engine.py` | new test class `TestHysteresis` |
| **0.5** | Implement dead band logic | `decision_engine.py` | new tests |
| **0.6** | Implement priority-based conflict resolution | `decision_engine.py` | new test class `TestConflictResolution` |
| **0.7** | Move cooldown from SensorState to RuleCondition | `decision_engine.py`, `SensorState` | update existing tests |
| **0.8** | SQLite schema migration (new condition fields + priority) | `sqlite_client.py` | update `test_rules_crud.py` |
| **0.9** | Rule pair auto-merge migration script | new `migrate_rules.py` | `test_rule_migration.py` |
| **0.10** | Simulation A/B comparison (bang-bang vs hysteresis) | `simulations/runner.py`, new comparison CLI | `test_simulation_control.py` |
| **0.11** | Update YAML loader + API to support new fields | `decision_engine.py`, `api/cortex.py` | update existing tests |

---

## Energy Model

The plan references "energy cost" throughout — but measuring in "actuator-minutes" is meaningless when a 15W circulation fan and a 1000W HPS light are both counted as "1 minute." We need wattage on each actuator to compute real energy costs.

### Actuator Wattage

Extend the existing `Actuator` dataclass with a `watts` field:

```python
@dataclass
class Actuator:
    id: str
    type: str
    pin: int | None = None
    name: str | None = None
    state: bool | None = None
    watts: float | None = None  # NEW: rated power draw in watts
```

**Set via:**
1. **Device birth message** — ESP32 includes `watts` in capabilities JSON (preferred: hardware knows its own load)
2. **API/UI** — User sets watts per relay in the dashboard (e.g., "relay2 = Exhaust Fan, 45W")
3. **Defaults by type** — Fallback estimates when neither is set:

```python
DEFAULT_WATTS_BY_TYPE = {
    "fan": 25.0,
    "exhaust_fan": 45.0,
    "humidifier": 30.0,
    "dehumidifier": 200.0,
    "irrigation": 15.0,     # solenoid valve or small pump
    "light": 400.0,         # HPS/LED panel — varies wildly, user should set
    "heater": 1000.0,
}
```

### Energy Computation

Replace "actuator-minutes" with watt-hours:

```python
@dataclass
class EnergySnapshot:
    """Energy consumption for a time window."""
    total_wh: float                         # total watt-hours consumed
    per_actuator_wh: dict[str, float]       # relay2 → 3.75 Wh
    per_actuator_runtime_min: dict[str, float]  # relay2 → 5.0 min (still useful)

def compute_energy(
    actuator_runtime: dict[str, float],    # relay_id → minutes ON in window
    actuator_watts: dict[str, float],      # relay_id → watts
) -> EnergySnapshot:
    per_actuator_wh = {}
    for relay_id, minutes in actuator_runtime.items():
        watts = actuator_watts.get(relay_id, 25.0)  # fallback 25W
        per_actuator_wh[relay_id] = watts * (minutes / 60.0)

    return EnergySnapshot(
        total_wh=sum(per_actuator_wh.values()),
        per_actuator_wh=per_actuator_wh,
        per_actuator_runtime_min=actuator_runtime,
    )
```

### Where Energy Feeds In

| Consumer | How It Uses Energy |
|----------|-------------------|
| **Ecosystem Health** | `energy_cost` field becomes `total_wh` not actuator-minutes. Efficiency strategy penalty scales with real watts. |
| **Effect Profiles** | `estimate_energy(command)` returns predicted Wh for the actuator based on learned average runtime × watts. |
| **Advisor Context** | "Fan ran 3.75 Wh in past 6h" is actionable. "Fan ran 5 actuator-minutes" is not. |
| **Simulation** | Track cumulative Wh per strategy. "Precision used 2.4 kWh/day, efficiency used 0.8 kWh/day." |
| **Dashboard** | Real-time and historical energy display. Cost estimation if $/kWh is configured. |

### Optional: Cost Estimation

A simple multiplier on the profile or global config:

```python
# On cortex_profiles or global config
energy_rate: float | None = None  # $/kWh — optional, for cost display only
```

If set, dashboard can show "Today: 1.8 kWh ($0.22)" alongside health score. Not used in any decision logic — purely informational.

### Storage

Energy snapshots are embedded in the existing `cortex_health.detail` JSON — no new table needed:

```json
{
  "goal_scores": {"temp1": 1.0, "vpd": 0.85, ...},
  "out_of_range": ["vpd"],
  "energy": {
    "total_wh": 12.5,
    "per_actuator_wh": {"relay2": 3.75, "relay6": 6.67, ...},
    "per_actuator_runtime_min": {"relay2": 5.0, "relay6": 1.0, ...}
  }
}
```

---

## Phase 1: Grow Profiles, Goals & Ecosystem Health

### 1a. Grow Profiles

A **grow profile** defines what this environment is trying to do. It's the top-level intent that shapes everything underneath.

```sql
CREATE TABLE cortex_profiles (
    id          TEXT PRIMARY KEY,       -- UUID
    location    TEXT NOT NULL,          -- "zone-01" — matches MQTT topic location
    name        TEXT NOT NULL,          -- "Northern Lights - Flower", "Basil Seedlings"
    strategy    TEXT NOT NULL DEFAULT 'balanced',  -- "precision" | "balanced" | "efficiency"
    phase       TEXT,                   -- current growth phase: "seedling" | "veg" | "flower" | "late_flower" | "dry" | "cure"
    phase_start TEXT,                   -- ISO date when current phase began
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    UNIQUE(location)                    -- one active profile per location
);
```

**Strategy determines tradeoff behavior:**

| Strategy | Behavior |
|----------|----------|
| `precision` | Hit targets exactly. Spend energy freely. Every goal is high-priority. For dialed-in strain-specific grows. |
| `balanced` | Hit targets, but avoid wasteful actuator cycling. Tolerate being near the edge of a range if it avoids firing two competing actuators. |
| `efficiency` | Widen tolerances. Accept "good enough" if it means running fewer relays. Prioritize energy savings over tight control. |

The strategy is a **multiplier on how aggressively the system corrects** — not a separate set of rules.

### 1b. Goal Definitions

Goals belong to a profile and define per-phase targets. They support **raw sensors**, **derived metrics** (VPD, dry-back rate), and **relay schedules**.

```sql
CREATE TABLE cortex_goals (
    id          TEXT PRIMARY KEY,       -- UUID
    profile_id  TEXT NOT NULL REFERENCES cortex_profiles(id) ON DELETE CASCADE,
    metric      TEXT NOT NULL,          -- "temp1", "hum1", "vpd", "dry_back_rate", "relay6"
    metric_type TEXT NOT NULL DEFAULT 'sensor',  -- "sensor" | "derived" | "relay_schedule"
    phase       TEXT,                   -- NULL = all phases, or "flower", "veg", etc.
    range_min   REAL,                   -- NULL = no lower bound
    range_max   REAL,                   -- NULL = no upper bound
    tolerance   REAL DEFAULT 0.0,       -- acceptable overshoot before penalizing (strategy-adjusted)
    priority    REAL DEFAULT 1.0,       -- weight in ecosystem score
    schedule    TEXT,                   -- JSON: time-based ranges or relay ON/OFF windows
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
```

**Examples:**

```yaml
# Raw sensor goal — basic range
- metric: temp1
  metric_type: sensor
  phase: flower
  range_min: 24.0
  range_max: 28.0
  priority: 1.0
  schedule:
    "22:00-06:00": {range_min: 20.0, range_max: 24.0}  # lights-off drop

# Derived metric — VPD (computed from temp + humidity)
- metric: vpd
  metric_type: derived
  phase: flower
  range_min: 1.0
  range_max: 1.3
  priority: 1.5  # VPD matters more than individual temp/humidity

# Derived metric — dry-back rate (soil moisture delta over time)
- metric: dry_back_rate
  metric_type: derived
  phase: flower
  range_min: 2.0     # % per hour — too slow means overwatered
  range_max: 8.0     # too fast means underwatered
  priority: 0.8

# Relay schedule — lights should be ON during this window
- metric: relay6
  metric_type: relay_schedule
  schedule:
    "06:00-22:00": {expected: true}    # ON during day
    "22:00-06:00": {expected: false}   # OFF at night
  priority: 1.0
```

### 1c. Derived Metrics

New module `derived_metrics.py` computes values that don't come from sensors directly:

```python
DERIVED_METRICS = {
    "vpd": {
        "inputs": ["temp1", "hum1"],
        "compute": lambda readings: vpd_from_temp_rh(readings["temp1"], readings["hum1"]),
        "unit": "kPa",
        "label": "Vapor Pressure Deficit",
    },
    "dry_back_rate": {
        "inputs": ["soil1"],
        "compute": lambda readings, history: soil_delta_per_hour(history["soil1"]),
        "unit": "%/hr",
        "label": "Dry-Back Rate",
    },
    "dli": {
        "inputs": ["light1"],
        "compute": lambda readings, history: daily_light_integral(history["light1"]),
        "unit": "mol/m²/day",
        "label": "Daily Light Integral",
    },
}

def vpd_from_temp_rh(temp_c: float, rh_pct: float) -> float:
    """Tetens equation: SVP from temp, then VPD from SVP and RH."""
    svp = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    vpd = svp * (1 - rh_pct / 100)
    return round(vpd, 3)
```

VPD is the key example — it **couples** temperature and humidity into a single metric that matters more than either alone for plant transpiration. A goal on VPD means the system has to reason about temp and humidity together, not as independent variables.

### 1d. Strategy-Adjusted Tolerance

The `tolerance` field on goals widens or narrows based on the profile strategy:

```python
STRATEGY_TOLERANCE_MULTIPLIER = {
    "precision":  0.0,   # no tolerance — exact range
    "balanced":   1.0,   # use the goal's configured tolerance
    "efficiency": 2.0,   # double the tolerance — accept wider range
}

def effective_range(goal: Goal, strategy: str) -> tuple[float, float]:
    mult = STRATEGY_TOLERANCE_MULTIPLIER[strategy]
    margin = goal.tolerance * mult
    return (goal.range_min - margin, goal.range_max + margin)
```

In `efficiency` mode, a temp goal of 24-28°C with tolerance=1.0 becomes 22-30°C — the system won't fire actuators for a reading of 28.5°C. In `precision` mode, 28.1°C triggers immediate correction.

### 1e. Ecosystem Health Score

A periodic job (every 60s) samples all sensors, computes derived metrics, and scores against the active profile's goals for the current phase:

```python
@dataclass
class EcosystemHealth:
    score: float                          # 0.0 to 1.0 — weighted compliance
    goal_scores: dict[str, float]         # per-metric compliance (0.0 to 1.0)
    out_of_range: list[str]               # metrics currently outside their goal
    energy: EnergySnapshot                # real Wh, not actuator-minutes
    timestamp: int

def compute_health(
    location: str,
    profile: GrowProfile,
    goals: list[Goal],
    readings: dict[str, float],           # aggregated across all devices in location
    relay_states: dict[str, bool],
    actuator_runtime: dict[str, float],   # minutes ON in current window
    actuator_watts: dict[str, float],     # relay_id → rated watts
) -> EcosystemHealth:
    # 1. Compute derived metrics and merge with raw readings
    all_metrics = {**readings}
    for name, spec in DERIVED_METRICS.items():
        if all(s in readings for s in spec["inputs"]):
            all_metrics[name] = spec["compute"](readings)

    # 2. Score each goal
    goal_scores = {}
    for goal in goals:
        if goal.phase and goal.phase != profile.phase:
            continue  # skip goals for other phases

        effective = effective_range(goal, profile.strategy)

        if goal.metric_type == "relay_schedule":
            # Binary: is relay in expected state?
            expected = goal.get_expected_state(now)
            actual = relay_states.get(goal.metric, False)
            goal_scores[goal.metric] = 1.0 if actual == expected else 0.0

        elif goal.metric in all_metrics:
            value = all_metrics[goal.metric]
            if effective[0] <= value <= effective[1]:
                goal_scores[goal.metric] = 1.0
            else:
                distance = min(
                    abs(value - effective[0]) if value < effective[0] else float('inf'),
                    abs(value - effective[1]) if value > effective[1] else float('inf'),
                )
                scale = effective[1] - effective[0]
                goal_scores[goal.metric] = max(0.0, 1.0 - (distance / scale)) if scale > 0 else 0.0

    # 3. Weighted average
    total = sum(goal_scores.get(g.metric, 0) * g.priority for g in goals if g.metric in goal_scores)
    total_weight = sum(g.priority for g in goals if g.metric in goal_scores)
    score = total / total_weight if total_weight > 0 else 0.0

    # 4. Compute energy
    energy = compute_energy(actuator_runtime, actuator_watts)

    # 5. Energy penalty (efficiency strategy only)
    if profile.strategy == "efficiency":
        # Scale penalty by real watts — 100Wh/hr is significant, 5Wh/hr is not
        energy_penalty = min(0.1, energy.total_wh * 0.001)
        score = max(0.0, score - energy_penalty)

    return EcosystemHealth(
        score=score,
        goal_scores=goal_scores,
        out_of_range=[m for m, s in goal_scores.items() if s < 1.0],
        energy=energy,
        timestamp=now_ms(),
    )
```

**Storage** — `cortex_health` table:

```sql
CREATE TABLE cortex_health (
    location    TEXT NOT NULL,          -- matches profile location
    ts          INTEGER NOT NULL,
    score       REAL NOT NULL,          -- 0.0 to 1.0
    detail      TEXT NOT NULL,           -- JSON: per-metric scores, out_of_range, energy_cost
    PRIMARY KEY (location, ts)
);
```

### 1f. What "Effective" Now Means

Effectiveness is no longer per-command. It's measured at the ecosystem level:

- **Goal compliance**: % of time each metric is in-range over a window (1h/6h/24h)
- **Ecosystem stability**: How often does health score stay above 0.8? How much does it oscillate?
- **Recovery time**: When a metric goes out of range, how quickly does the system bring it back?
- **Energy efficiency**: How many Wh did it take to maintain health above 0.8? What's the cost per day?
- **VPD tracking**: Are temp and humidity being managed together (VPD in range) or fighting each other?

---

## Phase 2: Effect Profiles (Learned Cross-Variable Impact)

### The Problem

When the exhaust fan turns ON, the system only tracks temperature. But the fan also:
- Lowers humidity (-0.5%/min in simulation)
- Indirectly accelerates soil drying (via humidity drop)

The system should **learn these relationships from observed data**, not from hardcoded physics.

### 2a. Multi-Sensor Outcome Snapshots

When any command fires, snapshot **all** sensors (not just the target):

```python
@dataclass
class EffectSnapshot:
    correlation_id: str
    device_id: str
    actuator: str          # "relay2"
    action_value: bool     # True = ON
    rule_name: str
    pre_readings: dict[str, float]   # ALL sensors at command time
    post_readings_1m: dict[str, float] | None
    post_readings_5m: dict[str, float] | None
    post_readings_10m: dict[str, float] | None
```

### 2b. Effect Profile Aggregation

After collecting N snapshots for an actuator, compute the average multi-sensor delta:

```python
@dataclass
class EffectProfile:
    actuator: str
    action: str            # "ON" or "OFF"
    sample_count: int
    effects: dict[str, SensorEffect]  # sensor_id → effect

@dataclass
class SensorEffect:
    avg_delta_1m: float    # average change at 1min
    avg_delta_5m: float    # average change at 5min
    avg_delta_10m: float   # average change at 10min
    std_dev: float         # consistency of effect
    confidence: float      # sample_count / required_minimum
```

**Storage** — `cortex_effects` table:

```sql
CREATE TABLE cortex_effects (
    device_id   TEXT NOT NULL,
    actuator    TEXT NOT NULL,
    action      TEXT NOT NULL,       -- "on" / "off"
    sensor      TEXT NOT NULL,
    avg_delta_5m REAL NOT NULL,
    std_dev     REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (device_id, actuator, action, sensor)
);
```

**Example learned profile after 20+ observations:**
```
relay2 ON:
  temp1:  avg_delta_5m = -1.2°C,  std_dev = 0.3  (confident: consistent)
  hum1:   avg_delta_5m = -2.1%,   std_dev = 0.8  (confident: consistent)
  soil1:  avg_delta_5m = -0.3%,   std_dev = 0.5  (low confidence: indirect effect)
```

Now Cortex **knows** that turning on relay2 affects three sensors, not just temperature.

### 2c. Side-Effect Awareness

With effect profiles, the Rule Advisor gains new capabilities:

- **Conflict detection**: "Rule A turns on relay2 to lower temperature, but this will also lower humidity — and humidity is already at the low end of its goal range."
- **Synergy detection**: "Rule B turns on relay2 for humidity, and this also helps temperature — both goals benefit."
- **Net impact scoring**: Before firing a command, estimate total ecosystem health delta across all goals.

---

## Phase 3: Goal-Aware Decision Making

### 3a. Pre-Fire Impact Estimation

Before the decision engine fires a command, estimate the **net ecosystem impact** using learned effect profiles and the active grow profile:

```python
def estimate_impact(
    command: Command,
    readings: dict[str, float],
    effects: EffectProfile,
    goals: list[Goal],
    profile: GrowProfile,
) -> ImpactEstimate:
    """Predict what firing this command will do to ecosystem health."""
    predicted_readings = dict(readings)

    # Apply learned multi-sensor effects
    for sensor, effect in effects.effects.items():
        if effect.confidence >= 0.5:  # only trust effects with enough data
            predicted_readings[sensor] = readings.get(sensor, 0) + effect.avg_delta_5m

    # Recompute derived metrics with predicted values
    predicted_vpd = vpd_from_temp_rh(predicted_readings.get("temp1", 0),
                                      predicted_readings.get("hum1", 0))
    predicted_readings["vpd"] = predicted_vpd

    # Score current vs predicted health
    current_score = score_against_goals(readings, goals, profile)
    predicted_score = score_against_goals(predicted_readings, goals, profile)

    # Track which goals improve and which degrade
    improvements = []
    degradations = []
    for goal in goals:
        cur = goal_compliance(readings, goal, profile)
        pred = goal_compliance(predicted_readings, goal, profile)
        if pred > cur:
            improvements.append((goal.metric, cur, pred))
        elif pred < cur:
            degradations.append((goal.metric, cur, pred))

    return ImpactEstimate(
        net_delta=predicted_score - current_score,
        improvements=improvements,
        degradations=degradations,
        energy_cost=estimate_energy(command),
    )
```

**Decision behavior based on strategy:**

| Strategy | Net delta < 0 | Net delta ~0 | Net delta > 0 |
|----------|--------------|-------------|--------------|
| `precision` | Fire anyway if primary goal improves | Fire | Fire |
| `balanced` | Skip + log conflict | Fire if no degradations | Fire |
| `efficiency` | Skip + log conflict | Skip (save energy) | Fire if energy-justified |

### 3b. Conflict Resolution

When impact estimation detects a degradation, the system can:

1. **Skip** — Don't fire. Another rule or natural drift may resolve it.
2. **Pair** — Fire, but also queue a compensating action (e.g., fan + humidifier together).
3. **Defer** — Wait N minutes and re-evaluate. The situation may resolve itself.
4. **Escalate** — Flag for LLM with full ecosystem context to reason about tradeoffs.

```python
@dataclass
class ConflictEvent:
    device_id: str
    rule_name: str
    command: Command
    impact: ImpactEstimate
    resolution: str           # "skipped" | "paired" | "deferred" | "escalated"
    paired_command: Command | None
    timestamp: int
```

Conflicts are stored and surfaced to the Rule Advisor — patterns of repeated conflicts indicate rules that need restructuring.

### 3c. Recovery Time Tracking

Track how quickly the system recovers when a metric leaves its goal range:

```python
@dataclass
class RecoveryEvent:
    device_id: str
    metric: str               # "temp1", "vpd", "soil1"
    exit_value: float         # value when it left range
    goal_min: float
    goal_max: float
    exit_ts: int
    recovery_ts: int | None   # NULL if still out of range
    recovery_ms: int | None
    commands_during: list[str] # correlation_ids of commands fired during recovery

# Tracked in-memory, persisted on recovery or timeout
```

Recovery time is the metric that matters: not "did the fan lower temperature" but "how quickly did the ecosystem return to healthy state, and at what energy cost?"

---

## Phase 4: Rule Advisor Enhancement

The Rule Advisor currently sees flat per-command effectiveness scores. With grow profiles, ecosystem health, and effect profiles, it gains the context to reason about the system as a whole.

### 4a. Advisor Context (Before vs After)

**Current — per-command, single-sensor:**
```
Rule "high_temp_exhaust_on": 15 outcomes, avg effectiveness +0.65
Rule "low_humidity_humidifier_on": 8 outcomes, avg effectiveness +0.42
```

The advisor can only say "tweak thresholds." It has no idea these rules are fighting each other.

**New — ecosystem-aware, profile-driven:**
```
GROW PROFILE: "Northern Lights - Flower" (precision strategy, flower since 2026-01-20)
  VPD target: 1.0–1.3 kPa (priority 1.5)
  temp target: 24–28°C day / 20–24°C night (priority 1.0)
  humidity target: 45–55% (priority 1.0)
  soil moisture target: 40–65% (priority 0.8)

ECOSYSTEM HEALTH (past 6h):
  Overall: 78%
  Per-metric compliance: temp 91%, humidity 62%, vpd 71%, soil 88%
  Weakest: humidity — out of range 38% of the time
  VPD unstable — oscillating between 0.85 and 1.4 kPa (fan cycling)

EFFECT PROFILES (learned from 45 observations):
  relay2 ON (exhaust fan): temp -1.2°C/5m, humidity -2.1%/5m, soil -0.3%/5m
  relay3 ON (humidifier):  humidity +3.2%/5m, temp +0.2%/5m
  relay5 ON (irrigation):  soil +8.5%/5m, humidity +1.2%/5m

CONFLICT LOG (past 6h):
  12 conflicts — relay2 (fan) fired for temp, degraded humidity 10 times
  Fan+humidifier cycling: relay2 ON → humidity drops → relay3 ON → temp rises → relay2 ON
  Net energy waste: 35 Wh in oscillation (fan 45W × 47min)

RECOVERY TIMES (past 6h):
  temp1: 3 excursions, avg recovery 2.5min (fast ✓)
  hum1:  7 excursions, avg recovery 8.1min (slow — fan keeps pulling it down)
  vpd:   9 excursions, avg recovery 6.3min (unstable — coupled to temp/hum oscillation)
```

### 4b. New Suggestion Types

The advisor can now produce suggestions the old system couldn't:

| Suggestion Type | Example | Old System |
|----------------|---------|------------|
| **Threshold tweak** | "Lower high_temp threshold from 28→27 to reduce fan aggression" | ✅ Had this |
| **Conflict resolution** | "Fan and humidifier are cycling — add a 10min cooldown between them" | ❌ Couldn't see it |
| **Rule pairing** | "When fan fires, also enable humidifier for 3min to compensate humidity loss" | ❌ No cross-variable awareness |
| **Strategy adjustment** | "Humidity can't stay in 45-55% with current actuators. Widen tolerance to 40-60% or switch to balanced strategy" | ❌ No goals concept |
| **Phase transition** | "Day 28 of flower. Late-flower targets (lower humidity, higher VPD) would be appropriate now" | ❌ No growth phases |
| **Derived metric focus** | "VPD is oscillating because temp and humidity rules don't coordinate. Consider a single VPD-driven rule instead of separate temp/humidity rules" | ❌ No derived metrics |
| **Energy optimization** | "Fan used 135 Wh in past 6h (45W × 180min). Raising temp threshold by 1°C would save 54 Wh with <2% health impact" | ❌ No energy tracking |

### 4c. Advisor Prompt Structure

```python
def build_advisor_prompt(
    profile: GrowProfile,
    goals: list[Goal],
    health_history: list[EcosystemHealth],   # past 6h, sampled every 60s
    effect_profiles: list[EffectProfile],
    conflicts: list[ConflictEvent],
    recoveries: list[RecoveryEvent],
    rules: list[Rule],
) -> str:
    return f"""You are the Rule Advisor for an automated grow environment.

OBJECTIVE: {profile.name}
Strategy: {profile.strategy} | Phase: {profile.phase} (since {profile.phase_start})

GOALS (current phase):
{format_goals(goals, profile)}

ECOSYSTEM HEALTH (past 6h):
{format_health_summary(health_history)}

LEARNED ACTUATOR EFFECTS:
{format_effect_profiles(effect_profiles)}

CONFLICTS (past 6h):
{format_conflicts(conflicts)}

RECOVERY PERFORMANCE:
{format_recoveries(recoveries)}

CURRENT RULES:
{format_rules(rules)}

Analyze the ecosystem and suggest improvements. Consider:
1. Are any rules fighting each other? Can they be coordinated?
2. Is the strategy appropriate for current conditions?
3. Should any goals be adjusted for the current growth phase?
4. Are there energy savings available without sacrificing health?
5. Would derived-metric rules (VPD-driven) replace conflicting individual rules?

Return suggestions as JSON array...
"""
```

### 4d. Suggestion Lifecycle Changes

Suggestions now include impact estimates:

```python
@dataclass
class EcosystemSuggestion:
    id: str
    rule_name: str | None          # NULL for new-rule or strategy suggestions
    suggestion_type: str            # "threshold" | "conflict" | "pairing" | "strategy" | "phase" | "energy"
    description: str
    confidence: float
    estimated_health_impact: float  # predicted change to ecosystem health score
    estimated_energy_impact: float  # predicted change to actuator-minutes/hour
    affected_metrics: list[str]     # which goals this touches
    status: str                     # "pending" | "applied" | "rejected"
```

Auto-apply rules (confidence >= 0.8) still apply for threshold tweaks. New suggestion types (conflict resolution, rule pairing, strategy changes) always require manual approval — they're too architectural to auto-apply.

---

## Phase 5: Simulation Integration

The simulation framework already models cross-variable physics. With ecosystem effectiveness, it becomes a first-class testing and optimization tool.

### 5a. Goals in Simulation

The simulation runner accepts a grow profile + goals and scores ecosystem health at each timestep:

```python
class SimulationRunner:
    def __init__(self, profile: GrowProfile, goals: list[Goal], ...):
        self.profile = profile
        self.goals = goals
        self.health_history: list[EcosystemHealth] = []

    def step(self, dt: float):
        # ... existing physics + rule evaluation ...

        # Score ecosystem health this tick
        health = compute_health(
            location=self.location,
            profile=self.profile,
            goals=self.goals,
            readings=self.environment.get_readings(),
            relay_states=self.environment.get_relay_states(),
            actuator_runtime=self.environment.get_runtime(),
            actuator_watts=self.environment.get_actuator_watts(),
        )
        self.health_history.append(health)
```

### 5b. Simulation Output

Instead of just sensor timeseries, simulation produces:
- **Health score over time** — Did the system converge? Where did it struggle?
- **Per-goal compliance** — Which metric was hardest to maintain?
- **Conflict count** — How many actuator-vs-actuator fights occurred?
- **Energy usage** — Total Wh/kWh and per-relay breakdown (watts × runtime)
- **VPD stability** — Derived metric tracking through the simulation

### 5c. Strategy Comparison

Run the same scenario with different strategies to see tradeoffs:

```bash
# Compare strategies for the same grow profile
python -m simulations.run --profile "northern-lights-flower" --strategy precision
python -m simulations.run --profile "northern-lights-flower" --strategy balanced
python -m simulations.run --profile "northern-lights-flower" --strategy efficiency
```

Output: side-by-side charts showing health score, energy usage, and per-metric compliance for each strategy.

---

## Migration Path

### What Gets Removed
- `OutcomeTracker` per-command effectiveness scoring (1m/5m/10m single-sensor)
- `cortex_outcomes` table (replaced by `cortex_effects` + `cortex_health`)
- Keyword-based target metric inference (`METRIC_KEYWORD_MAP`, `ON_DIRECTION_BY_METRIC`)
- `ON_DIRECTION_BY_METRIC`, `METRIC_SCALE_FACTORS` — no longer needed when scoring ecosystem-wide
- Per-command effectiveness scores in Rule Advisor context
- Effectiveness guard logic (replaced by ecosystem health trends)

### What Gets Preserved
- `OutcomeTracker.track_command()` lifecycle — refactored to snapshot all sensors
- `cortex_suggestions` table and approve/reject flow — extended with new suggestion types
- Rule Advisor analysis loop — enhanced with ecosystem context
- Baseline tracking (`CortexMemory`) — still needed for per-sensor hourly baselines + anomaly detection
- All forecasting utilities (`forecaster.py`) — still needed for per-sensor projection
- Decision engine rule evaluation — extended with pre-fire impact estimation
- Simulation physics engine — enhanced with health scoring

### What Gets Added

| Module | Description |
|--------|-------------|
| `cortex_profiles` table | Grow profile per location (strategy, phase, active) |
| `cortex_goals` table | Per-profile goals with phase/schedule/tolerance |
| `cortex_health` table | Periodic ecosystem health scores |
| `cortex_effects` table | Learned per-actuator multi-sensor effect profiles |
| `Actuator.watts` field | Rated wattage per relay for real energy computation |
| `derived_metrics.py` | VPD, dry-back rate, DLI computation |
| `ecosystem_health.py` | Health scoring engine, recovery tracking |
| `effect_tracker.py` | Multi-sensor snapshot collection, profile aggregation |
| `grow_profiles.py` | Profile + goals CRUD, phase management |
| API: `/api/cortex/profiles` | Profile CRUD + phase transitions |
| API: `/api/cortex/goals` | Goal CRUD per profile |
| API: `/api/cortex/health` | Health history + current score |
| API: `/api/cortex/effects` | Learned effect profiles |
| WS: `{type: "health"}` | Real-time health score broadcast |
| Decision engine | Pre-fire impact estimation, conflict logging |
| Rule Advisor | Ecosystem-aware prompt, new suggestion types |
| Simulation | Health scoring, strategy comparison, goal tracking |

---

## Implementation Order

| Step | Description | New/Modified Files | Tests |
|------|-------------|-------------------|-------|
| **1** | Derived metrics module (VPD, dry-back, DLI) | `services/derived_metrics.py` | `test_derived_metrics.py` |
| **2** | Grow profiles table + CRUD | `sqlite_client.py`, `services/grow_profiles.py`, `api/cortex.py` | `test_grow_profiles.py` |
| **3** | Goals table + CRUD + phase filtering | `sqlite_client.py`, `services/grow_profiles.py`, `api/cortex.py` | `test_goals.py` |
| **4** | Ecosystem health scoring engine | `services/ecosystem_health.py`, `background_jobs.py` | `test_ecosystem_health.py` |
| **5** | Multi-sensor effect snapshots (refactor OutcomeTracker) | `services/effect_tracker.py`, `sqlite_client.py` | `test_effect_tracker.py` |
| **6** | Effect profile aggregation + storage | `services/effect_tracker.py`, `sqlite_client.py` | `test_effect_tracker.py` |
| **7** | Pre-fire impact estimation in decision engine | `services/decision_engine.py` | update `test_decision_engine.py` |
| **8** | Conflict detection + resolution logic | `services/decision_engine.py` | `test_conflicts.py` |
| **9** | Recovery time tracking | `services/ecosystem_health.py` | `test_recovery.py` |
| **10** | Rule Advisor ecosystem prompt + new suggestion types | `services/rule_advisor.py` | update `test_rule_advisor.py` |
| **11** | REST API endpoints (profiles, goals, health, effects) | `api/cortex.py` | update `test_cortex_api.py` |
| **12** | WebSocket health broadcast | `services/websocket_server.py` | — |
| **13** | Dashboard: health score display, goals UI, profile selector | `apps/web/src/` | — |
| **14** | Simulation: health scoring + strategy comparison | `simulations/runner.py`, `simulations/charts.py` | update simulation tests |
| **15** | Remove old OutcomeTracker scoring + `cortex_outcomes` table | `outcome_tracker.py`, `sqlite_client.py` | cleanup old tests |

---

## Intelligence Architecture

The rules engine is the execution layer — reflexes. The LLM is underutilized if its only job is tweaking thresholds. This section defines four intelligence layers that elevate Cortex from "fancy thermostat" to autonomous grow strategist.

### Intelligence Layers

| Layer | Timescale | Owner | Role |
|-------|-----------|-------|------|
| **L1: Reflexes** | ms–seconds | Rules Engine | Deterministic control. Hysteresis, dead bands, priority. No LLM in the hot path. Phase 0 covers this. |
| **L2: Tactical Advisor** | minutes–hours | Rule Advisor + LLM | Observe outcomes → suggest/create/remove/restructure rules. Currently limited to threshold tweaks — evolves into full rule architect. |
| **L3: Grow Planner** | days–weeks | LLM + Profiles/Goals | Strategic planning. Generates week-by-week goal schedules per strain/phase. Proposes phase transitions. Adjusts strategy based on observed plant development. |
| **L4: Knowledge Engine** | on-demand | LLM + RAG | Domain expertise. Strain genetics, grow guides, research papers, VPD science, nutrient schedules. Answers "why" questions, not just "what." |

### L2: Tactical Advisor (Evolution)

The Rule Advisor today says "move 28 to 27." With ecosystem context (Phases 1-3), it should be able to:

**Generate rules** — Not just tweak thresholds, but propose entirely new rules:
```
"VPD is oscillating because temp and humidity rules fight each other.
 Proposing new rule: vpd_exhaust_control — triggers on VPD > 1.4 kPa
 instead of separate temp/humidity thresholds. This replaces
 high_temp_exhaust_on and low_humidity_humidifier_on."
```

**Remove rules** — Identify rules that are counterproductive or redundant:
```
"Rule 'low_humidity_humidifier_on' fires 12x/day but humidity recovers
 naturally within 8 minutes. Removing this rule saves 30 Wh/day with
 <3% health impact."
```

**Restructure control strategy** — Suggest pairing, reordering, or replacing rule sets:
```
"Fan + humidifier are cycling. Suggesting: add 10-minute mutual exclusion
 cooldown, or replace both with a single VPD-driven rule."
```

New suggestion types for L2:
- `rule_create` — propose a new rule (full condition + action)
- `rule_remove` — propose deleting a rule with impact estimate
- `rule_restructure` — propose replacing N rules with M rules
- `mutual_exclusion` — propose cooldown coupling between rules

### L3: Grow Planner (New Layer)

This is the biggest intelligence gap. Nobody is answering strategic questions today.

**What it does:**
1. **Profile generation** — User says "Northern Lights, 4x4 tent, coco coir, HPS 600W." LLM generates a complete grow profile with phase-specific goals, VPD targets, light schedules, and irrigation timing.
2. **Phase transition proposals** — "Day 28 of flower. Based on typical indica-dominant timelines, late-flower targets (lower humidity, higher VPD, reduced nitrogen) are appropriate. Proposing phase transition."
3. **Week-over-week planning** — Generates a goal schedule: "Week 5 flower: VPD 1.2-1.4, temp 24-26°C day / 20-22°C night, humidity 40-50%, begin dry-back cycles."
4. **Multi-grow learning** — Compares current grow to previous grows at the same location. "Last run, calcium lockout appeared in week 6 when pH drifted below 5.8. Current pH trend suggests monitoring."

**Architecture:**
```
User: "Starting Northern Lights in flower"
  → LLM (with strain knowledge + location history)
  → Generates: GrowProfile + list[Goal] per phase
  → Stored in cortex_profiles + cortex_goals
  → Rules engine reads goals for health scoring
  → Advisor reads goals for suggestion context

Weekly background job:
  → LLM reviews: health history, phase timeline, strain expectations
  → Proposes: goal adjustments, phase transitions, strategy changes
  → Surfaces as suggestions (type: "phase" | "strategy" | "goal_adjust")
```

**Integration points:**
- `POST /api/cortex/profiles/generate` — LLM generates profile from natural language description
- `POST /api/chat` — "How's my grow?" includes health score, phase context, comparison to plan
- Advisor prompt includes growth timeline and strain expectations alongside ecosystem data
- Phase transitions proposed as suggestions with confidence scores — manual approval initially

### L4: Knowledge Engine (Future)

RAG over domain knowledge. Not needed for Phase 1, but the architecture should leave room:

**Sources:**
- Strain databases (genetics, flowering time, preferred VPD/temp ranges)
- Grow guides and feeding schedules (Floraflex, General Hydroponics, etc.)
- Research papers (VPD science, light spectrum effects, dry-back protocols)
- User's own grow journals and historical data

**Integration:**
- Chat can answer: "What's the ideal VPD for Northern Lights in week 5 of flower?"
- Advisor context can include: "Strain reference: NL typically finishes 56-63 days, prefers VPD 1.0-1.3 in mid-flower"
- Profile generator uses strain knowledge to set initial goals

**Not needed now — but the data model (profiles + goals + health) is the foundation this plugs into.**

### How This Connects to the Phases

| Phase | Intelligence Layer Enabled |
|-------|---------------------------|
| Phase 0 (done) | L1 — solid deterministic control |
| Phase 1 (profiles + goals + health) | **Foundation for L2, L3, L4** — gives the LLM structured data to reason about |
| Phase 2 (effect profiles) | L2 — advisor understands cross-variable actuator impact |
| Phase 3 (goal-aware decisions) | L2 — advisor can reason about tradeoffs, not just thresholds |
| Phase 4 (enhanced advisor) | L2 full evolution — rule architect, not just threshold tweaker |
| Phase 5 (simulation) | L3 — test strategies before deploying, compare grow plans |
| Future | L3 grow planner, L4 knowledge engine |

Phase 1 is the critical enabler — it creates the structured vocabulary (profiles, goals, health scores) that every intelligence layer needs to communicate. Without it, the LLM has nothing to plan against and no way to express strategic intent.

---

## Decisions (Resolved)

1. **Effect profiles: per-device.** Each environment has unique physical characteristics (room size, ventilation, duct length). The system learns each independently.
2. **Default grow profile.** Ship a "Generic Balanced" profile with sensible defaults (temp 22-28°C, humidity 40-65%, VPD 0.8-1.4 kPa). Users customize from there.
3. **Phase transitions: manual to start.** User explicitly switches phases via UI/API. Architecture leaves room for advisor-suggested transitions later (suggestion_type: "phase").
4. **Health score in chat context: yes.** `interpret_message()` includes current ecosystem health so the LLM can answer "how's my grow doing?" with real data.
5. **Profiles by location, not device.** A profile covers a location/zone (e.g., "zone-01") and all devices within it. Multiple sensor nodes + relay controllers in the same zone share one profile and one set of goals. This aligns with the existing MQTT topic structure: `home/{location}/{deviceId}/...`

### Location-Scoped Profiles (Decision 5 Detail)

The `cortex_profiles` table changes from per-device to per-location:

```sql
CREATE TABLE cortex_profiles (
    id          TEXT PRIMARY KEY,       -- UUID
    location    TEXT NOT NULL,          -- "zone-01" — matches MQTT topic location
    name        TEXT NOT NULL,          -- "Northern Lights - Flower"
    strategy    TEXT NOT NULL DEFAULT 'balanced',
    phase       TEXT,
    phase_start TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    UNIQUE(location)                    -- one active profile per location
);
```

**Implications:**
- Health scoring aggregates all devices in a location — uses Coordinator to read cross-device sensors
- Goals reference sensor types (e.g., "temp1") not specific device+sensor pairs — if two nodes both report temp1, use the average (or worst-case, depending on strategy)
- Effect profiles remain per-device (relay2 on device-A may behave differently than relay2 on device-B even in the same zone)
- The existing `scope: "all"` / `scope: "any"` rule conditions align naturally — location is the implicit scope boundary
- Device registry already tracks which location each device belongs to via MQTT birth topics
