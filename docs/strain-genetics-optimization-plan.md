# Strain Genetics Optimization Plan

> Goal: Enable Cortex to optimize the control plane precisely for strain-specific genetics across all growth phases.

## Current State Assessment

### What's Working (Phases 1-8)

The cybernetic control loop is functionally complete:

```
Sensors → Baselines → Gap Analysis → Suggestions → Auto-Apply → Outcomes → Repeat
```

| Capability | Status | Key Insight |
|---|---|---|
| Hysteresis rules with day/night awareness | ✅ | 4 rule sets (hysteresis, bang-bang, suboptimal, diurnal) |
| Effect profiles (cross-variable learning) | ✅ | Welford's incremental stats, 10-sample confidence |
| Ecosystem health scoring | ✅ | Strategy-adjusted tolerance (precision/balanced/efficiency) |
| Impact estimation (pre-fire filtering) | ✅ | Predicts health delta before commanding actuators |
| Deterministic advisor | ✅ | Catches unreachable/too-sensitive thresholds without LLM |
| Derived metrics | ✅ | VPD (Tetens), dry-back rate, DLI all computable |
| Grow profiles & goals | ✅ | Per-location strategy, phase, and metric goals |
| Recovery tracking | ✅ | Monitors in-range → out → back transitions |

### Systemic Gaps

#### 1. No Plant Model
The physics engine models the **tent** but not the **plant**. No transpiration, no stomatal conductance, no growth rate feedback. The system optimizes environmental compliance against static goal ranges, not actual plant response.

#### 2. VPD Is Computed but Never Targeted
- No VPD goal exists in any simulation scenario
- No rule can target VPD directly (rules check single raw sensors only)
- No compound conditions (can't express "if VPD > 1.4, do X")

#### 3. Simulation Blindspots
- `context["history"]` is hardcoded to `None` in the runner — dry-back rate and DLI are never computed
- Effect aggregation is reimplemented locally (`EffectAggregator`) instead of using `EffectTracker` — divergence risk
- Humidity equilibrium drifts to 50% — real tents with active canopy sit at 60-80%

#### 4. No Strain Awareness
- Profiles have `phase` but no `strain`/`cultivar` field
- Goals are manually created, no templates per strain+phase
- No grow cycle tracking (planted → harvested date range)
- Phase transitions are manual — no automatic detection

#### 5. Rules Can't Express What Strains Need
- Single-sensor conditions only — no "temp > 28 AND humidity > 65"
- Binary ON/OFF only — no proportional control
- No rule chaining ("if exhaust ran 10 min without effect, escalate")
- Advisor can only adjust numeric thresholds, can't restructure rules

---

## Phase 9: Strain Genetics Foundation

**Goal**: Establish the data model that everything else builds on. Without strain data, the system has no target to optimize *toward*.

### 9a. `cortex_strains` Table

Store cultivar genetics data with per-phase ideal ranges.

```sql
CREATE TABLE cortex_strains (
    id          TEXT PRIMARY KEY,        -- UUID
    name        TEXT NOT NULL UNIQUE,    -- e.g. "Blue Dream", "Northern Lights"
    breeder     TEXT,                    -- optional breeder/seed bank
    type        TEXT NOT NULL,           -- "photoperiod" | "autoflower" | "ruderalis"
    lineage     TEXT,                    -- e.g. "Blueberry x Haze"
    notes       TEXT,                    -- free-form cultivation notes
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);
```

### 9b. `cortex_strain_phases` Table

Per-phase ideal ranges for all metrics, parameterized by strain.

```sql
CREATE TABLE cortex_strain_phases (
    id              TEXT PRIMARY KEY,
    strain_id       TEXT NOT NULL REFERENCES cortex_strains(id) ON DELETE CASCADE,
    phase           TEXT NOT NULL,       -- seedling|clone|veg|transition|flower|late_flower|dry|cure
    days_min        INTEGER,             -- typical minimum days in this phase
    days_max        INTEGER,             -- typical maximum days in this phase
    photoperiod_on  INTEGER,             -- light hours (e.g. 18 for veg, 12 for flower)
    photoperiod_off INTEGER,             -- dark hours
    -- Ideal ranges per metric
    temp_min        REAL, temp_max       REAL,
    temp_night_min  REAL, temp_night_max REAL,  -- night differential
    humidity_min    REAL, humidity_max    REAL,
    vpd_min         REAL, vpd_max        REAL,
    dli_min         REAL, dli_max        REAL,   -- mol/m²/day
    soil_min        REAL, soil_max       REAL,
    dry_back_min    REAL, dry_back_max   REAL,   -- %/hr target
    co2_min         REAL, co2_max        REAL,   -- ppm (future)
    -- Sensitivity weights (how much this strain cares about each metric)
    temp_sensitivity    REAL DEFAULT 1.0,
    humidity_sensitivity REAL DEFAULT 1.0,
    vpd_sensitivity     REAL DEFAULT 1.0,
    UNIQUE(strain_id, phase)
);
```

### 9c. Add `strain_id` to Profiles

```sql
ALTER TABLE cortex_profiles ADD COLUMN strain_id TEXT REFERENCES cortex_strains(id);
```

When a profile has a `strain_id` and `phase`, goals can be auto-populated from `cortex_strain_phases`.

### 9d. Goal Templates

`auto_populate_goals(profile_id)` — reads strain+phase, generates goals from `cortex_strain_phases`, including:
- Raw sensor goals (temp, humidity, soil moisture)
- Derived metric goals (VPD, DLI, dry-back rate)
- Relay schedule goals (light photoperiod)

### 9e. Grow Cycle Tracking

```sql
CREATE TABLE cortex_grows (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL REFERENCES cortex_profiles(id),
    strain_id   TEXT NOT NULL REFERENCES cortex_strains(id),
    start_date  TEXT NOT NULL,
    end_date    TEXT,                    -- NULL = active grow
    phase_log   TEXT,                    -- JSON array of {phase, start, end} transitions
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
```

Enables cross-cycle comparison: "Last Northern Lights grow averaged 92% health in flower. This one is at 87% — what's different?"

### 9f. API Endpoints

```
GET    /api/cortex/strains              — List all strains
GET    /api/cortex/strains/:id          — Single strain with phases
POST   /api/cortex/strains              — Create strain
PUT    /api/cortex/strains/:id          — Update strain
DELETE /api/cortex/strains/:id          — Delete strain (cascade phases)

GET    /api/cortex/strains/:id/phases   — Phase ranges for a strain
POST   /api/cortex/strains/:id/phases   — Add phase data
PUT    /api/cortex/strain-phases/:id    — Update phase data
DELETE /api/cortex/strain-phases/:id    — Delete phase data

POST   /api/cortex/profiles/:id/auto-goals  — Auto-populate goals from strain+phase
GET    /api/cortex/grows                — List grow cycles
POST   /api/cortex/grows               — Start a grow cycle
PUT    /api/cortex/grows/:id            — Update grow (end date, phase transition)
```

### 9g. Simulation Integration

- Add a `STRAINS` dict to `simulations/scenarios/grow_tent.py` with 2-3 reference strains (indica-dominant, sativa-dominant, autoflower)
- `SimulationRunner` accepts a strain parameter and auto-generates goals from strain phases
- Phase transitions can be simulated (e.g., switch from veg to flower at hour 720)

### 9h. Tests

- `test_strain_crud.py` — CRUD operations, cascade delete, unique constraints
- `test_goal_templates.py` — auto-populate from strain+phase, phase filtering, derived metric goals
- `test_grow_cycles.py` — lifecycle, phase transitions, cross-cycle queries

---

## Phase 10: VPD-Centric Control

**Goal**: Make VPD a first-class control target rather than a passive metric.

### 10a. Derived Metric Rules

Allow rules to reference derived metrics (e.g., `vpd`) as the sensor field. The decision engine computes derived metrics on-the-fly during rule evaluation from raw readings.

### 10b. VPD Goals in Simulation

Add VPD goals to all simulation scenarios:
- Veg: 0.8-1.2 kPa (priority 1.0)
- Flower: 1.0-1.5 kPa (priority 1.0)
- Late flower: 1.2-1.6 kPa

### 10c. Fix Simulation History Context

Pass rolling history into `context["history"]` so dry-back rate and DLI are computed during simulation.

### 10d. Strain-Specific Leaf Temperature Offset

VPD accuracy depends on leaf temperature. Add `leaf_temp_offset` to `cortex_strain_phases` (default -2.0°C). Apply offset in `DerivedMetricEngine.compute_vpd()`.

### 10e. Compound Conditions (Optional)

Allow rules to specify multiple sensor conditions via an `and_conditions` array:
```yaml
condition:
  sensor: vpd
  operator: ">"
  threshold: 1.5
  and_conditions:
    - sensor: hum1
      operator: ">"
      threshold: 70
```

---

## Phase 11: Plant-Aware Physics

**Goal**: Make the simulation respond to plant presence, not just hardware.

### 11a. Transpiration Model

```python
class PlantModel:
    lai: float           # Leaf Area Index (strain-dependent, increases over time)
    stomatal_conductance: float  # strain-dependent

    def transpiration_rate(self, vpd: float, light_on: bool) -> float:
        """Returns humidity contribution in %/min from plant transpiration."""
        # Penman-Monteith simplified: ET ∝ LAI × stomatal_conductance × VPD × light_factor
```

### 11b. Humidity Equilibrium Fix

Replace static 50% equilibrium with dynamic equilibrium driven by:
- Plant transpiration (adds humidity)
- Ambient outdoor humidity
- Tent leakage rate

### 11c. Growth Stage Progression

LAI increases over time based on strain growth rate, affecting transpiration and humidity coupling. Phase transitions shift the plant model parameters.

### 11d. Photoperiod Variants

Support 18/6 (veg), 12/12 (flower), and custom schedules per strain+phase.

---

## Phase 12: Intelligent Rule Evolution

**Goal**: The advisor becomes capable of structural changes, not just threshold tuning.

### 12a. Advisor Structural Suggestions

New suggestion types:
- "Add time-of-day condition to rule X"
- "Convert bang-bang pair to hysteresis rule"
- "Add VPD compound condition"

### 12b. Phase-Transition Triggers

When a profile's phase changes (manual or auto-detected):
1. Load new goals from strain+phase template
2. Advisor proactively adjusts thresholds toward new phase targets
3. Log the transition in `cortex_grows.phase_log`

### 12c. Multi-Cycle Learning

Compare outcomes across grows of the same strain:
- Which environmental profiles produced the best health scores?
- What rule configurations converged fastest?
- Identify strain-specific quirks (e.g., "Northern Lights always needs 2°C lower night temp than database suggests")

### 12d. Strain-Specific Advisor Context

The advisor receives strain data in its analysis context:
- "This strain prefers VPD 1.0-1.2 in veg. Current baselines show VPD averaging 1.4."
- Enables suggestions that move toward strain-specific optima, not just statistical stability.

---

## Priority & Dependencies

```
Phase 9 (Strain Foundation)  ← START HERE — everything builds on this
    ↓
Phase 10 (VPD Control)       ← Unlocks precision targeting
    ↓
Phase 11 (Plant Physics)     ← Makes simulation strain-realistic
    ↓
Phase 12 (Rule Evolution)    ← Full autonomous optimization
```

Phase 9 is the data model. Phase 10 makes it useful. Phase 11 makes simulations accurate. Phase 12 closes the loop.