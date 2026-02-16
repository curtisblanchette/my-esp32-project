# Full System Refactor: Generic Sensor Telemetry Pipeline

## Context

The rule generation pipeline and chat prompts were already made generic (340 tests pass). But the **entire telemetry pipeline** — from MQTT ingestion through Redis/SQLite storage to the React frontend — is hardcoded for `temp` and `humidity` only. The ESP32 firmware already sends generic `{id, value, unit}` readings, so the device side needs no changes. This refactor makes the backend and frontend sensor-type agnostic.

**Root cause**: `mqtt_client.py` line 168 extracts only `"temp1"` and `"hum1"`, then stores them as `RedisReading(temp=, humidity=)`. Everything downstream inherits these fixed fields.

---

## Schema Decision: EAV `sensor_values` Table

New table with one row per sensor per timestamp:
```sql
CREATE TABLE sensor_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    sensor_id TEXT NOT NULL,
    value REAL NOT NULL,
    source_topic TEXT
);
```
**Why**: Cleanest for per-sensor queries, aggregation, and baseline lookups. Aligns with how data is produced (N readings per batch) and consumed (one sensor at a time for rules/trends/forecasts).

---

## Phase A: Backend Storage Foundation

### A1. New `sensor_values` table + migration
**File**: `apps/cortex/src/services/sqlite_client.py`
- Add `SensorValue` dataclass: `(ts, device_id, sensor_id, value, source_topic)`
- Add `sensor_values` table to `connect()` schema with indices on `(device_id, sensor_id, ts)` and `(device_id, ts)`
- Migration in `_run_migrations()`: copy `sensor_readings` → `sensor_values` (temp→temp1, humidity→hum1)
- New methods: `insert_sensor_values()`, `query_sensor_values()`, `query_sensor_values_bucketed()`
- Keep old `TelemetryRow`/`insert_reading()`/`query_history_raw()` temporarily

### A2. Generic `RedisReading`
**File**: `apps/cortex/src/services/redis_client.py`
- Change `RedisReading`: replace `temp: float, humidity: float` → `readings: dict[str, float]`
- Update `to_dict()`: serialize `readings` dict
- Update `get_readings_in_range()` / `get_all_readings()`: backward-compat parsing (old `{temp, humidity}` → `{temp1: val, hum1: val}`)

### A3. Generic `MergedReading` + `DataReader`
**File**: `apps/cortex/src/services/data_reader.py`
- Change `MergedReading`: replace `temp: float, humidity: float` → `readings: dict[str, float]`
- Update `get_readings()`: merge from `sensor_values` table (group by ts,device_id) + Redis
- Update `extract_metric(readings, sensor_id)`: extract by sensor_id key from `readings` dict

### A4. Generic aggregation
**File**: `apps/cortex/src/services/background_jobs.py`
- `_aggregate_and_flush()`: bucket by `(bucket_ts, device_id, sensor_id)` instead of flat temp/humidity sums
- Write to `sensor_values` via `insert_sensor_values()`

### A5. Tests
- **New**: `tests/test_sensor_values.py` — CRUD, bucketing, migration
- **Update**: `tests/test_data_reader.py` — new `MergedReading` shape, `extract_metric()` by sensor_id
- **Update**: `tests/conftest.py` — `mock_redis` uses new `RedisReading` format

---

## Phase B: Backend Consumers

### B1. MQTT telemetry handler (the entry point)
**File**: `apps/cortex/src/services/mqtt_client.py`
- `_handle_telemetry()`: loop ALL readings in payload, not just temp1/hum1
- Build `readings_dict: dict[str, float]` from all numeric readings
- Store `RedisReading(ts, readings=readings_dict, ...)`
- Broadcast `{readings: {...}, updatedAt, deviceId}` via WebSocket

### B2. Analysis module
**File**: `apps/cortex/src/services/analysis.py`
- `analyze_sensor_data()`: iterate all sensor keys generically, use `guess_sensor_type()` for metric names
- `format_analysis_reply()`: use `sensor_unit()` / `sensor_label()` from `sensor_meta.py`

### B3. Orchestrator context builder
**File**: `apps/cortex/src/main.py` (lines 310-445)
- `_build_context()`: discover sensor IDs from readings, iterate generically for trends/forecasts/baselines
- `_update_baselines()`: iterate `telemetry.readings` generically with `guess_sensor_type()`
- Effectiveness: query all actuators from device capabilities, not hardcoded `["relay1"]`

### B4. Outcome Tracker
**File**: `apps/cortex/src/services/outcome_tracker.py`
- `_get_current_value()`: find sensor by type via `guess_sensor_type()` from `MergedReading.readings`
- `check_outcomes()` / `_finalize_outcome()` / `_complete_outcome()`: resolve sensor_id from pre_snapshot keys
- `_infer_target_metric()`: extend with all `SENSOR_TYPE_DEFAULTS` types
- `SCALE_FACTORS`: add soil_moisture, light_level, co2, pressure

### B5. Rule Advisor
**File**: `apps/cortex/src/services/rule_advisor.py`
- Replace `SENSOR_TO_METRIC` dict with `guess_sensor_type()` calls
- `_build_analysis_prompt()`: use `sensor_unit()` instead of hardcoded `"°C"` / `"%"`

### B6. Coordinator
**File**: `apps/cortex/src/services/coordinator.py`
- Remove `SENSOR_TO_KEY` mapping entirely
- `get_latest_reading()`: read from `latest.get("readings", {}).get(sensor_id)`
- `get_all_latest_readings()`: same pattern

### B7. Telemetry API
**File**: `apps/cortex/src/api/telemetry.py`
- Bucketed mode: generic per-sensor bucketing from `sensor_values` + Redis
- Raw mode: query `sensor_values`, group by `(ts, device_id)` into `{ts, readings: {...}}`
- Response shape changes: `{ts, temp, humidity}` → `{ts, readings: {temp1: val, hum1: val}}`

### B8. Intent Executor (analyze + query paths)
**File**: `apps/cortex/src/services/intent_executor.py`
- `analyze` intent: use `data_reader.get_readings()` with new `MergedReading` format
- `query` intent: read from `latest.get("readings", {}).get(sensor_id)`, remove legacy `_SENSOR_TO_KEY`

### B9. Tests
- Update: `test_data_reader.py`, `test_analysis.py`, `test_outcome_tracker.py`, `test_coordinator.py`, `test_rule_advisor.py`, `test_cortex_api.py`, `test_cross_device_rules.py`, `conftest.py`

---

## Phase C: Frontend

### C1. Types and API
**File**: `apps/web/src/api.ts`
- `LatestReading`: `{readings: Record<string, number>, updatedAt, ...}` (replace `temp`/`humidity`)
- `HistoryPoint`: `{ts, readings: Record<string, number>, count?}`
- Replace `hasTempHumiditySensors()` → `hasSensors()` (any sensors)
- Add `getSensorsByType(device, type)` utility

### C2. useHistory hook
**File**: `apps/web/src/hooks/useHistory.ts`
- `appendReading()`: create `HistoryPoint` with `readings` dict from `LatestReading`

### C3. SensorCard → generic sensor grid
**File**: `apps/web/src/components/SensorCard.tsx`
- Props: `{sensors: Sensor[], latestReading, deviceId}` (not `temp`/`humidity` fields)
- Keep temp circle + humidity water-fill as type-specific sub-components
- Add `GenericSensorReadout` for other types (soil_moisture, contact, light_level, etc.)
- Charts: render one per sensor from capabilities

### C4. DevicePanel
**File**: `apps/web/src/components/DevicePanel.tsx`
- Remove `tempToMix()`, `clamp()`, hardcoded thresholds and comfort messages
- Use `hasSensors(device)` instead of `hasTempHumiditySensors(device)`
- Pass `sensors` array + `latestReading` to new SensorCard

### C5. NerveCenterBaselines
**File**: `apps/web/src/components/NerveCenterBaselines.tsx`
- Discover unique metrics from baseline data dynamically
- Render one chart per metric with color palette from `METRIC_COLORS` map
- Use `guessSensorUnit()` for axis labels

### C6. TypeScript verification
- `cd apps/web && npx tsc --noEmit`
- `npm run build`

---

## Phase D: Cleanup

- Remove old `sensor_readings` references (keep table as backup, don't DROP)
- Remove `TelemetryRow`, `TelemetryBucketRow` dataclasses
- Remove old `insert_reading()`, `query_history_raw()`, `query_history_bucketed()` methods
- Remove backward-compat `{temp, humidity}` parsing from `RedisReading`
- Remove `hasTempHumiditySensors()`, legacy `_SENSOR_TO_KEY` mappings
- Remove `_LEGACY_MAP` / `_LEGACY_KEY_MAP` from `ollama_client.py` / `rule_generator.py`

---

## Files Modified (by phase)

| Phase | File | Change |
|-------|------|--------|
| A1 | `sqlite_client.py` | New `sensor_values` table, `SensorValue` dataclass, migration, CRUD |
| A2 | `redis_client.py` | `RedisReading.readings: dict`, backward-compat parsing |
| A3 | `data_reader.py` | `MergedReading.readings: dict`, generic `extract_metric()` |
| A4 | `background_jobs.py` | Per-sensor-id aggregation |
| B1 | `mqtt_client.py` | Generic telemetry extraction |
| B2 | `analysis.py` | Generic `analyze_sensor_data()`, `format_analysis_reply()` |
| B3 | `main.py` | Dynamic sensor iteration in context builder + baselines |
| B4 | `outcome_tracker.py` | Generic sensor resolution, extensible scale factors |
| B5 | `rule_advisor.py` | Replace `SENSOR_TO_METRIC` with `guess_sensor_type()` |
| B6 | `coordinator.py` | Remove `SENSOR_TO_KEY`, read from `readings` dict |
| B7 | `telemetry.py` | Generic bucketing, new response shape |
| B8 | `intent_executor.py` | Generic analyze + query paths |
| C1 | `api.ts` | `LatestReading`, `HistoryPoint` type changes |
| C2 | `useHistory.ts` | Generic `appendReading()` |
| C3 | `SensorCard.tsx` | Type-based gauge rendering, generic readout |
| C4 | `DevicePanel.tsx` | Remove hardcoded thresholds, generic sensor display |
| C5 | `NerveCenterBaselines.tsx` | Dynamic per-metric chart rendering |

---

## Verification

1. `cd apps/cortex && pytest tests/ -m "not e2e"` — all tests pass after each phase
2. `cd apps/web && npx tsc --noEmit` — frontend type-checks after Phase C
3. `npm run build` — production build succeeds
4. Manual: connect a device with diverse sensors, verify readings appear on dashboard
5. Manual: rule generation works for non-temp/humidity sensor types
6. Manual: baselines chart shows all learned metrics, not just temp/humidity
