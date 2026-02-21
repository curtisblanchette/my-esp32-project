# Phase 2: Effect Profiles — Learning Multi-Sensor Actuator Impact

## Executive Summary

Add cross-variable effect learning to Cortex by tracking how each actuator affects ALL sensors (not just the target). When relay2 (exhaust fan) fires, learn that it affects temp1 (-0.3°C/min), hum1 (-0.5%/min), AND soil1 (indirect). Store these learned multi-sensor effects so the advisor and decision engine can reason about side effects and tradeoffs.

**Key constraint:** DO NOT break existing OutcomeTracker. The effect tracker runs alongside it, capturing the same snapshots but aggregating differently.

---

## Architecture

### Data Model

```sql
CREATE TABLE cortex_effects (
    device_id TEXT NOT NULL,
    actuator TEXT NOT NULL,          -- "relay2", "relay3", etc.
    action TEXT NOT NULL,             -- "on" | "off"
    sensor TEXT NOT NULL,             -- "temp1", "hum1", "soil1", etc.
    avg_delta_5m REAL NOT NULL,       -- average change at 5min (primary signal)
    std_dev REAL NOT NULL,            -- consistency (Welford's algorithm)
    sample_count INTEGER NOT NULL,    -- observations used
    sum_values REAL NOT NULL,         -- Welford: sum of deltas
    sum_squares REAL NOT NULL,        -- Welford: sum of squared deltas
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (device_id, actuator, action, sensor)
);

CREATE INDEX IF NOT EXISTS idx_effects_device_actuator
    ON cortex_effects(device_id, actuator);
```

### Core Types

```python
@dataclass
class EffectSnapshot:
    """Multi-sensor snapshot for a command."""
    correlation_id: str
    device_id: str
    actuator: str                    # "relay2"
    action_value: bool               # True = ON, False = OFF
    rule_name: str
    command_ts: int
    pre_readings: dict[str, float]   # ALL sensors at command time
    post_readings_5m: dict[str, float] | None  # ALL sensors at 5min

@dataclass
class SensorEffect:
    """Learned effect on a single sensor."""
    avg_delta: float                 # average change at 5min
    std_dev: float                   # consistency
    sample_count: int                # how many observations
    confidence: float                # sample_count / MIN_SAMPLES (e.g., 10)

@dataclass
class EffectProfile:
    """Complete learned profile for an actuator action."""
    device_id: str
    actuator: str
    action: str                      # "on" | "off"
    effects: dict[str, SensorEffect] # sensor_id → effect
```

---

## Implementation

### 1. New Module: `effect_tracker.py`

**Location:** `apps/cortex/src/services/effect_tracker.py`

#### Core Class

```python
class EffectTracker:
    """Tracks multi-sensor effects of actuator commands.
    
    Runs alongside OutcomeTracker, capturing the same snapshots but
    aggregating into per-actuator effect profiles using Welford's algorithm.
    """
    
    def __init__(self, sqlite: SqliteClient, data_reader: DataReader):
        self._sqlite = sqlite
        self._data_reader = data_reader
        self._pending: dict[str, EffectSnapshot] = {}
        self._ensure_tables()
    
    def _ensure_tables(self) -> None:
        """Create cortex_effects table."""
        # SQL from above
    
    def track_command(self, command: Command, telemetry: TelemetryMessage) -> None:
        """Start tracking multi-sensor effects for a command.
        
        IMPORTANT: This runs alongside OutcomeTracker.track_command().
        Both capture the same pre-snapshot.
        """
        if not command.correlation_id:
            return
        
        # Build pre-snapshot from ALL numeric sensors
        pre_readings: dict[str, float] = {}
        for reading in telemetry.readings:
            if isinstance(reading.value, (int, float)):
                pre_readings[reading.id] = float(reading.value)
        
        if not pre_readings:
            return
        
        snapshot = EffectSnapshot(
            correlation_id=command.correlation_id,
            device_id=command.device_id,
            actuator=command.target,
            action_value=bool(command.value),
            rule_name=command.reason or "unknown",
            command_ts=int(time.time() * 1000),
            pre_readings=pre_readings,
            post_readings_5m=None,
        )
        
        self._pending[command.correlation_id] = snapshot
    
    def check_snapshots(self) -> int:
        """Check pending snapshots and collect post-readings at 5min.
        
        Returns: number of completed snapshots aggregated.
        """
        now = time.time()
        completed_count = 0
        to_remove: list[str] = []
        
        for cid, snapshot in self._pending.items():
            command_time = snapshot.command_ts / 1000.0
            check_time_5m = command_time + 300  # 5 minutes
            
            if now < check_time_5m:
                continue
            
            # Collect post-snapshot for ALL sensors
            post_readings = self._get_current_readings(snapshot.device_id)
            if not post_readings:
                to_remove.append(cid)
                continue
            
            snapshot.post_readings_5m = post_readings
            
            # Aggregate into effect profile
            self._aggregate_effects(snapshot)
            completed_count += 1
            to_remove.append(cid)
        
        for cid in to_remove:
            del self._pending[cid]
        
        return completed_count
    
    def _get_current_readings(self, device_id: str) -> dict[str, float]:
        """Read current values for all sensors on a device."""
        try:
            readings = self._data_reader.get_recent_readings(
                window_minutes=2, device_id=device_id
            )
            if not readings:
                return {}
            
            # Return latest readings dict
            return dict(readings[-1].readings)
        except Exception as e:
            logger.error(f"Failed to read current readings for {device_id}: {e}")
            return {}
    
    def _aggregate_effects(self, snapshot: EffectSnapshot) -> None:
        """Incrementally update effect profile using Welford's algorithm.
        
        For each sensor that appears in both pre and post snapshots,
        compute the delta and update the running statistics.
        """
        if not snapshot.post_readings_5m:
            return
        
        action_str = "on" if snapshot.action_value else "off"
        
        for sensor_id in snapshot.pre_readings:
            pre_val = snapshot.pre_readings[sensor_id]
            post_val = snapshot.post_readings_5m.get(sensor_id)
            
            if post_val is None:
                continue
            
            delta = post_val - pre_val
            
            # Update effect profile in SQLite using Welford's
            self._update_effect(
                snapshot.device_id,
                snapshot.actuator,
                action_str,
                sensor_id,
                delta,
            )
    
    def _update_effect(
        self,
        device_id: str,
        actuator: str,
        action: str,
        sensor: str,
        delta: float,
    ) -> None:
        """Incrementally update effect statistics using Welford's algorithm.
        
        Same pattern as CortexMemory.update_baseline().
        """
        db = self._sqlite._get_db()
        now = int(time.time() * 1000)
        
        row = db.execute(
            "SELECT sample_count, sum_values, sum_squares FROM cortex_effects "
            "WHERE device_id = ? AND actuator = ? AND action = ? AND sensor = ?",
            (device_id, actuator, action, sensor),
        ).fetchone()
        
        if row:
            count = row["sample_count"] + 1
            sum_v = row["sum_values"] + delta
            sum_sq = row["sum_squares"] + delta * delta
        else:
            count = 1
            sum_v = delta
            sum_sq = delta * delta
        
        avg = sum_v / count
        variance = (sum_sq / count) - (avg * avg) if count > 1 else 0.0
        std_dev = math.sqrt(max(0, variance))
        
        db.execute(
            """
            INSERT INTO cortex_effects
                (device_id, actuator, action, sensor, avg_delta_5m, std_dev,
                 sample_count, sum_values, sum_squares, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (device_id, actuator, action, sensor) DO UPDATE SET
                avg_delta_5m = ?,
                std_dev = ?,
                sample_count = ?,
                sum_values = ?,
                sum_squares = ?,
                updated_at = ?
            """,
            (device_id, actuator, action, sensor, avg, std_dev, count, sum_v, sum_sq, now,
             avg, std_dev, count, sum_v, sum_sq, now),
        )
        db.commit()
    
    def get_effect_profile(
        self,
        device_id: str,
        actuator: str,
        action: str,
    ) -> EffectProfile | None:
        """Retrieve learned effect profile for an actuator action."""
        db = self._sqlite._get_db()
        cursor = db.execute(
            "SELECT sensor, avg_delta_5m, std_dev, sample_count "
            "FROM cortex_effects "
            "WHERE device_id = ? AND actuator = ? AND action = ? "
            "ORDER BY sensor",
            (device_id, actuator, action),
        )
        
        rows = cursor.fetchall()
        if not rows:
            return None
        
        MIN_SAMPLES = 10
        effects = {}
        for row in rows:
            confidence = min(1.0, row["sample_count"] / MIN_SAMPLES)
            effects[row["sensor"]] = SensorEffect(
                avg_delta=row["avg_delta_5m"],
                std_dev=row["std_dev"],
                sample_count=row["sample_count"],
                confidence=confidence,
            )
        
        return EffectProfile(
            device_id=device_id,
            actuator=actuator,
            action=action,
            effects=effects,
        )
    
    def query_effects(
        self,
        device_id: str | None = None,
        actuator: str | None = None,
        min_samples: int = 5,
    ) -> list[dict]:
        """Query effect profiles for API endpoint."""
        db = self._sqlite._get_db()
        
        sql = ("SELECT device_id, actuator, action, sensor, "
               "avg_delta_5m, std_dev, sample_count, updated_at "
               "FROM cortex_effects WHERE sample_count >= ?")
        params: list[Any] = [min_samples]
        
        if device_id:
            sql += " AND device_id = ?"
            params.append(device_id)
        if actuator:
            sql += " AND actuator = ?"
            params.append(actuator)
        
        sql += " ORDER BY device_id, actuator, action, sensor"
        
        cursor = db.execute(sql, params)
        return [
            {
                "deviceId": row["device_id"],
                "actuator": row["actuator"],
                "action": row["action"],
                "sensor": row["sensor"],
                "avgDelta5m": round(row["avg_delta_5m"], 3),
                "stdDev": round(row["std_dev"], 3),
                "sampleCount": row["sample_count"],
                "confidence": round(min(1.0, row["sample_count"] / 10), 2),
                "updatedAt": row["updated_at"],
            }
            for row in cursor.fetchall()
        ]
```

---

### 2. Integration: `main.py`

**Changes to Orchestrator:**

```python
class Orchestrator:
    def __init__(self):
        # ... existing fields ...
        self._effect_tracker = None  # NEW
    
    def start(self):
        # ... after initializing OutcomeTracker ...
        
        # Phase 2: Initialize EffectTracker alongside OutcomeTracker
        from .services.effect_tracker import EffectTracker
        self._effect_tracker = EffectTracker(sqlite, self._data_reader)
        logger.info("Phase 2: EffectTracker initialized")
    
    def _handle_telemetry(self, telemetry: TelemetryMessage):
        # ... existing context building and rule evaluation ...
        
        # Phase 2: check pending effect snapshots (alongside outcome checks)
        if self._effect_tracker:
            completed = self._effect_tracker.check_snapshots()
            if completed:
                logger.debug(f"EffectTracker: aggregated {completed} snapshot(s)")
    
    def _execute_command(self, command: Command):
        # ... existing command execution ...
        
        # Phase 2: track outcome (existing)
        if self._outcome_tracker and self._latest_telemetry:
            self._outcome_tracker.track_command(command, self._latest_telemetry)
        
        # Phase 2: track effect (NEW — runs alongside outcome tracker)
        if self._effect_tracker and self._latest_telemetry:
            self._effect_tracker.track_command(command, self._latest_telemetry)
```

---

### 3. API Endpoint: `GET /api/cortex/effects`

**Add to `api/cortex.py`:**

```python
@router.get("/effects")
def get_effects(
    device_id: str | None = None,
    actuator: str | None = None,
    min_samples: int = 5,
) -> dict:
    """Query learned effect profiles.
    
    Returns multi-sensor impact for each actuator action.
    """
    if not effect_tracker:
        return {"effects": []}
    
    effects = effect_tracker.query_effects(device_id, actuator, min_samples)
    return {"effects": effects}
```

**Wire up in router factory:**

```python
def create_cortex_router(
    sqlite: SqliteClient,
    outcome_tracker: OutcomeTracker,
    memory: CortexMemory,
    rule_advisor: RuleAdvisor,
    ws_server: WebSocketServer,
    engine: DecisionEngine,
    effect_tracker: EffectTracker,  # NEW parameter
) -> APIRouter:
    # ... existing setup ...
    
    # Make effect_tracker available to route handlers
    router.effect_tracker = effect_tracker
    
    return router
```

**Update `main.py` router mount:**

```python
app.include_router(
    create_cortex_router(
        sqlite, self._outcome_tracker, self._memory,
        self._rule_advisor, ws_server, self.engine,
        self._effect_tracker,  # NEW
    ),
    prefix="/api/cortex",
)
```

---

### 4. Simulation Integration

**Extend `simulations/runner.py`:**

```python
class SimulationRunner:
    def __init__(self, ...):
        # ... existing fields ...
        
        # NEW: effect tracking for simulation
        self._pending_effects: list[dict] = []
    
    def _apply_commands(self, commands, elapsed_minutes, step, readings):
        # ... existing command application ...
        
        for cmd in commands:
            # ... existing logic ...
            
            # Track effect snapshot (alongside outcome)
            if self.track_outcomes:
                self._track_effect(step, elapsed_minutes, rule_name, cmd, readings)
    
    def _track_effect(
        self, step: int, time_minutes: float, rule_name: str,
        cmd: Any, readings: dict[str, float],
    ) -> None:
        """Start tracking multi-sensor effect for simulation."""
        action_str = "on" if cmd.value else "off"
        
        self._pending_effects.append({
            "step": step,
            "time_minutes": time_minutes,
            "rule_name": rule_name,
            "actuator": cmd.target,
            "action": action_str,
            "pre_readings": dict(readings),  # ALL sensors
            "post_readings_5m": None,
        })
    
    def _check_pending_outcomes(self, step, readings, result):
        # ... existing outcome checking ...
        
        # NEW: check pending effect snapshots
        self._check_pending_effects(step, readings, result)
    
    def _check_pending_effects(self, step, readings, result):
        """Check effect snapshots at 10 steps (~5min at 30s/step)."""
        CHECK_INTERVAL_STEPS = 10
        
        for effect in self._pending_effects:
            if effect["post_readings_5m"] is not None:
                continue
            
            steps_elapsed = step - effect["step"]
            if steps_elapsed < CHECK_INTERVAL_STEPS:
                continue
            
            # Capture post-snapshot
            effect["post_readings_5m"] = dict(readings)
            
            # Aggregate into result (new field)
            if not hasattr(result, "effects"):
                result.effects = []
            
            # Compute deltas for all sensors
            deltas = {}
            for sensor_id in effect["pre_readings"]:
                pre_val = effect["pre_readings"][sensor_id]
                post_val = effect["post_readings_5m"].get(sensor_id)
                if post_val is not None:
                    deltas[sensor_id] = post_val - pre_val
            
            result.effects.append({
                "actuator": effect["actuator"],
                "action": effect["action"],
                "rule_name": effect["rule_name"],
                "time_minutes": effect["time_minutes"],
                "deltas": deltas,
            })
```

**Extend `SimulationResult` dataclass:**

```python
@dataclass
class SimulationResult:
    # ... existing fields ...
    effects: list[dict] = field(default_factory=list)  # NEW
```

---

### 5. Tests

#### `tests/test_effect_tracker.py`

```python
class TestEffectTracker:
    """Tests for multi-sensor effect tracking."""
    
    def test_track_command_captures_all_sensors(self, sqlite_db, mock_data_reader):
        """Track command should snapshot ALL numeric sensors."""
        tracker = EffectTracker(sqlite_db, mock_data_reader)
        
        telemetry = TelemetryMessage(
            version=1,
            ts=int(time.time() * 1000),
            device_id="test-device",
            location="room1",
            readings=[
                Reading(id="temp1", value=26.5),
                Reading(id="hum1", value=58.0),
                Reading(id="soil1", value=45.0),
            ],
        )
        
        cmd = Command(
            device_id="test-device",
            location="room1",
            target="relay2",
            action="set",
            value=True,
            reason="Temperature rising",
        )
        cmd.correlation_id = "test-001"
        
        tracker.track_command(cmd, telemetry)
        
        assert "test-001" in tracker._pending
        snapshot = tracker._pending["test-001"]
        assert snapshot.actuator == "relay2"
        assert snapshot.action_value is True
        assert len(snapshot.pre_readings) == 3
        assert snapshot.pre_readings["temp1"] == 26.5
        assert snapshot.pre_readings["hum1"] == 58.0
        assert snapshot.pre_readings["soil1"] == 45.0
    
    def test_check_snapshots_collects_post_readings(self, sqlite_db, mock_data_reader):
        """After 5min, collect post-snapshot for all sensors."""
        tracker = EffectTracker(sqlite_db, mock_data_reader)
        
        # Track command at t=0
        cmd = Command(
            device_id="test-device",
            location="room1",
            target="relay2",
            action="set",
            value=True,
        )
        cmd.correlation_id = "test-002"
        
        telemetry_pre = TelemetryMessage(
            version=1,
            ts=int(time.time() * 1000),
            device_id="test-device",
            location="room1",
            readings=[
                Reading(id="temp1", value=26.5),
                Reading(id="hum1", value=58.0),
            ],
        )
        
        tracker.track_command(cmd, telemetry_pre)
        
        # Mock time passage (5min = 300s)
        past_ts = int((time.time() - 301) * 1000)
        tracker._pending["test-002"].command_ts = past_ts
        
        # Mock post-readings at t=5min
        mock_data_reader.set_current(temp=25.3, humidity=55.8, device_id="test-device")
        
        # Check snapshots
        completed = tracker.check_snapshots()
        
        assert completed == 1
        assert "test-002" not in tracker._pending  # removed after completion
        
        # Verify effect was aggregated to DB
        profile = tracker.get_effect_profile("test-device", "relay2", "on")
        assert profile is not None
        assert "temp1" in profile.effects
        assert "hum1" in profile.effects
        
        # temp1: 26.5 → 25.3 = -1.2°C
        assert profile.effects["temp1"].avg_delta == pytest.approx(-1.2, abs=0.01)
        # hum1: 58.0 → 55.8 = -2.2%
        assert profile.effects["hum1"].avg_delta == pytest.approx(-2.2, abs=0.01)
    
    def test_welford_aggregation(self, sqlite_db, mock_data_reader):
        """Multiple observations update statistics incrementally."""
        tracker = EffectTracker(sqlite_db, mock_data_reader)
        
        # First observation: temp delta = -1.0
        tracker._update_effect("test-device", "relay2", "on", "temp1", -1.0)
        
        profile = tracker.get_effect_profile("test-device", "relay2", "on")
        assert profile.effects["temp1"].avg_delta == -1.0
        assert profile.effects["temp1"].sample_count == 1
        assert profile.effects["temp1"].confidence == pytest.approx(0.1, abs=0.01)
        
        # Second observation: temp delta = -1.2
        tracker._update_effect("test-device", "relay2", "on", "temp1", -1.2)
        
        profile = tracker.get_effect_profile("test-device", "relay2", "on")
        assert profile.effects["temp1"].avg_delta == pytest.approx(-1.1, abs=0.01)
        assert profile.effects["temp1"].sample_count == 2
        
        # After 10 samples, confidence reaches 1.0
        for delta in [-1.1, -1.3, -0.9, -1.0, -1.2, -1.1, -1.0, -1.2]:
            tracker._update_effect("test-device", "relay2", "on", "temp1", delta)
        
        profile = tracker.get_effect_profile("test-device", "relay2", "on")
        assert profile.effects["temp1"].sample_count == 10
        assert profile.effects["temp1"].confidence == 1.0
        assert profile.effects["temp1"].std_dev > 0  # variance computed
    
    def test_query_effects_api(self, sqlite_db, mock_data_reader):
        """API query returns formatted effect profiles."""
        tracker = EffectTracker(sqlite_db, mock_data_reader)
        
        # Seed some effects
        for i in range(12):
            tracker._update_effect("device-A", "relay2", "on", "temp1", -1.1 + (i * 0.05))
            tracker._update_effect("device-A", "relay2", "on", "hum1", -2.0 + (i * 0.1))
        
        # Query with min_samples=10
        effects = tracker.query_effects(device_id="device-A", min_samples=10)
        
        assert len(effects) == 2
        temp_effect = [e for e in effects if e["sensor"] == "temp1"][0]
        assert temp_effect["actuator"] == "relay2"
        assert temp_effect["action"] == "on"
        assert temp_effect["sampleCount"] == 12
        assert temp_effect["confidence"] >= 1.0
        assert "avgDelta5m" in temp_effect
        assert "stdDev" in temp_effect
```

#### `tests/test_simulation_effects.py`

```python
class TestSimulationEffects:
    """Tests for effect tracking in simulation."""
    
    def test_simulation_tracks_multi_sensor_effects(self):
        """Simulation captures cross-variable actuator impact."""
        from simulations.environment import GrowTentEnvironment
        from simulations.runner import SimulationRunner
        from simulations.scenarios.grow_tent_rules import GROW_TENT_RULES
        
        env = GrowTentEnvironment(
            temp=28.0,    # high temp → fan will fire
            humidity=60.0,
            soil_moisture=50.0,
        )
        
        runner = SimulationRunner(
            environment=env,
            rules=GROW_TENT_RULES,
            duration_minutes=30,
            step_seconds=30,
            track_outcomes=True,  # enables effect tracking too
        )
        
        result = runner.run()
        
        # Should have captured effect snapshots
        assert len(result.effects) > 0
        
        # Find exhaust fan effect
        fan_effects = [e for e in result.effects if e["actuator"] == "relay2"]
        assert len(fan_effects) > 0
        
        fan_effect = fan_effects[0]
        assert "deltas" in fan_effect
        
        # Fan should affect multiple sensors
        deltas = fan_effect["deltas"]
        assert "temp1" in deltas
        assert "hum1" in deltas
        
        # Exhaust fan lowers both temp and humidity
        assert deltas["temp1"] < 0
        assert deltas["hum1"] < 0
```

---

## File Summary

### New Files

| File | Purpose | Lines |
|------|---------|-------|
| `apps/cortex/src/services/effect_tracker.py` | EffectTracker class, multi-sensor snapshot collection, Welford's aggregation | ~300 |
| `apps/cortex/tests/test_effect_tracker.py` | Unit tests for EffectTracker | ~200 |
| `apps/cortex/tests/test_simulation_effects.py` | Integration tests for simulation effect tracking | ~50 |

### Modified Files

| File | Changes | Lines Changed |
|------|---------|---------------|
| `apps/cortex/src/main.py` | Add EffectTracker initialization, track effects in `_execute_command`, check snapshots in `_handle_telemetry` | +15 |
| `apps/cortex/src/api/cortex.py` | Add `/effects` endpoint, pass effect_tracker to router factory | +20 |
| `apps/cortex/simulations/runner.py` | Add `_pending_effects`, `_track_effect`, `_check_pending_effects` | +80 |
| `apps/cortex/simulations/runner.py` (dataclass) | Add `effects` field to `SimulationResult` | +1 |

---

## Implementation Steps

### Step 1: Create EffectTracker Module
**Files:** `apps/cortex/src/services/effect_tracker.py`
- [ ] Define `EffectSnapshot`, `SensorEffect`, `EffectProfile` dataclasses
- [ ] Implement `EffectTracker` class with `_ensure_tables()`
- [ ] Implement `track_command()` — capture pre-snapshot for all sensors
- [ ] Implement `check_snapshots()` — collect post-readings at 5min
- [ ] Implement `_aggregate_effects()` — compute deltas for all sensors
- [ ] Implement `_update_effect()` — Welford's incremental statistics (pattern from `cortex_memory.py`)
- [ ] Implement `get_effect_profile()` — retrieve learned profile
- [ ] Implement `query_effects()` — API query with filters

**Tests:** `apps/cortex/tests/test_effect_tracker.py`
- [ ] `test_track_command_captures_all_sensors`
- [ ] `test_check_snapshots_collects_post_readings`
- [ ] `test_welford_aggregation`
- [ ] `test_query_effects_api`

**Run:** `cd apps/cortex && pytest tests/test_effect_tracker.py -v`

---

### Step 2: Integrate with Main Orchestrator
**Files:** `apps/cortex/src/main.py`
- [ ] Add `_effect_tracker` field to `Orchestrator.__init__()`
- [ ] Initialize `EffectTracker` in `start()` after OutcomeTracker
- [ ] Call `effect_tracker.track_command()` in `_execute_command()` alongside `outcome_tracker.track_command()`
- [ ] Call `effect_tracker.check_snapshots()` in `_handle_telemetry()` alongside `outcome_tracker.check_outcomes()`

**Tests:** Existing orchestrator tests should pass unchanged (effect tracker is additive)

**Run:** `cd apps/cortex && pytest tests/test_decision_engine.py tests/test_outcome_tracker.py -v`

---

### Step 3: Add API Endpoint
**Files:** `apps/cortex/src/api/cortex.py`
- [ ] Add `GET /api/cortex/effects` route handler
- [ ] Update `create_cortex_router()` signature to accept `effect_tracker` parameter
- [ ] Pass `effect_tracker` to router in `main.py`

**Tests:** `apps/cortex/tests/test_cortex_api.py`
- [ ] `test_get_effects_empty` — returns empty list when no data
- [ ] `test_get_effects_with_data` — returns formatted profiles
- [ ] `test_get_effects_filters` — device_id, actuator, min_samples filters work

**Run:** `cd apps/cortex && pytest tests/test_cortex_api.py::test_get_effects -v`

---

### Step 4: Extend Simulation Runner
**Files:** `apps/cortex/simulations/runner.py`
- [ ] Add `_pending_effects: list[dict]` field to `SimulationRunner.__init__()`
- [ ] Add `effects: list[dict]` field to `SimulationResult` dataclass
- [ ] Implement `_track_effect()` — capture pre-snapshot in `_apply_commands()`
- [ ] Implement `_check_pending_effects()` — collect post-snapshot at 10 steps (~5min)
- [ ] Call `_check_pending_effects()` in `run()` loop alongside `_check_pending_outcomes()`

**Tests:** `apps/cortex/tests/test_simulation_effects.py`
- [ ] `test_simulation_tracks_multi_sensor_effects`
- [ ] `test_fan_affects_temp_and_humidity`
- [ ] `test_irrigation_affects_soil_and_humidity`

**Run:** `cd apps/cortex && pytest tests/test_simulation_effects.py -v`

---

### Step 5: Visualization (Optional)
**Files:** `apps/cortex/simulations/charts.py`
- [ ] Add `plot_effect_heatmap()` — visualize actuator→sensor impact matrix
- [ ] Extend `plot_simulation()` to include effect summary panel

**Not critical for Phase 2 MVP — can defer to Phase 3 (Goal-Aware Decisions)**

---

## Success Criteria

### Functional Requirements
- [ ] EffectTracker runs alongside OutcomeTracker without breaking existing functionality
- [ ] Multi-sensor snapshots captured for every command
- [ ] Effect profiles aggregated using Welford's algorithm (incremental stats)
- [ ] Stored in `cortex_effects` table with proper indexing
- [ ] API endpoint returns formatted effect profiles
- [ ] Simulation runner extends to track multi-sensor effects
- [ ] All unit tests pass (`pytest tests/ -m "not e2e"`)

### Data Quality
- [ ] Effect profiles converge to accurate values in simulation (10+ samples)
- [ ] Confidence scores reflect sample count correctly (min(1.0, count / 10))
- [ ] Std dev computed correctly (Welford's variance formula)
- [ ] Effect profiles persist across restarts (SQLite storage)

### Performance
- [ ] Effect tracking adds <5ms per command (negligible overhead)
- [ ] Background job checks pending snapshots efficiently (<10ms per cycle)
- [ ] SQLite writes batched or use `executemany()` if aggregating many effects at once

---

## Integration with Future Phases

### Phase 3: Goal-Aware Decisions
Effect profiles feed into pre-fire impact estimation:
```python
def estimate_impact(command, readings, effects, goals):
    """Predict ecosystem health delta using learned effects."""
    predicted_readings = dict(readings)
    
    # Apply learned multi-sensor effects
    profile = effects.get_effect_profile(command.device_id, command.target, "on")
    if profile:
        for sensor, effect in profile.effects.items():
            if effect.confidence >= 0.5:
                predicted_readings[sensor] += effect.avg_delta
    
    # Score current vs predicted health against goals
    current_score = score_against_goals(readings, goals)
    predicted_score = score_against_goals(predicted_readings, goals)
    
    return predicted_score - current_score
```

### Phase 4: Enhanced Rule Advisor
Effect profiles enable conflict detection:
```python
advisor_prompt += f"""
LEARNED ACTUATOR EFFECTS:
  relay2 ON (exhaust fan):
    temp1: -1.2°C ± 0.3  (confident, 24 samples)
    hum1:  -2.1% ± 0.8   (confident, 24 samples)
    soil1: -0.3% ± 0.5   (low confidence, indirect effect)
  
  relay3 ON (humidifier):
    hum1:  +3.2% ± 0.4   (confident, 18 samples)
    temp1: +0.2°C ± 0.1  (side effect, 18 samples)

CONFLICT DETECTED:
  Fan and humidifier are fighting over humidity control.
  Suggest: add mutual exclusion cooldown or merge into VPD-driven rule.
"""
```

---

## Risks & Mitigations

### Risk: Effect Snapshots Miss Sensor Readings
**Problem:** Post-snapshot at 5min may not find a recent reading if device went offline.

**Mitigation:** 
- Use 2-minute window in `_get_current_readings()` (matches OutcomeTracker pattern)
- Discard snapshot if post-readings unavailable (don't aggregate partial data)
- Log warning for missing sensors but don't block aggregation of available ones

### Risk: Indirect Effects Add Noise
**Problem:** Soil moisture might fluctuate due to unrelated factors (irrigation timing) when fan fires.

**Mitigation:**
- Confidence scores filter out low-sample effects
- Large std_dev flags unreliable effects
- Phase 3 pre-fire estimation can ignore effects with confidence < 0.5

### Risk: Welford's Algorithm Edge Cases
**Problem:** Single observation has undefined variance, division by zero.

**Mitigation:**
- `variance = (sum_sq / count) - (avg * avg) if count > 1 else 0.0`
- `std_dev = math.sqrt(max(0, variance))` — clamp negative variance to zero (floating point rounding)
- Same pattern already proven in `cortex_memory.py`

---

## Critical Files for Implementation

- **apps/cortex/src/services/effect_tracker.py** — Core module (NEW, ~300 lines)
- **apps/cortex/src/main.py** — Integration point (4 changes, ~15 lines)
- **apps/cortex/src/api/cortex.py** — API endpoint (1 route + factory change, ~20 lines)
- **apps/cortex/simulations/runner.py** — Simulation integration (3 methods, ~80 lines)
- **apps/cortex/tests/test_effect_tracker.py** — Unit tests (NEW, ~200 lines)
