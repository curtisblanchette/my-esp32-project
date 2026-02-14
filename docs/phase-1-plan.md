# Phase 1: Give Cortex Eyes on History

## Context

Cortex is currently blind to history. The decision engine only sees the current telemetry value and compares it against static thresholds. The LLM receives "Historical context" that's really just the last-seen value per sensor. Phase 1 gives Cortex trend awareness, rate-of-change analysis, baseline learning, and enriched LLM context — the foundation for the cybernetic feedback loop.

## What Already Exists (reuse, don't rebuild)

- **`analysis.py`** — `Stats`, `Anomaly`, `SensorAnalysis`, `calculate_stats()`, `analyze_metric()` (trend detection, anomaly detection, fluctuation scoring). Designed for on-demand chat analysis, not the real-time decision loop.
- **`decision_engine.py`** — `RuleCondition(sensor, operator, threshold, duration_seconds)`, `evaluate(telemetry)`, `should_escalate_to_llm()`, per-sensor state tracking with cooldown.
- **`sqlite_client.py`** — `query_history_raw()`, `query_history_bucketed()` for telemetry reads.
- **`redis_client.py`** — `get_readings_in_range()` for hot data reads.
- **`intent_executor.py`** — manually merges Redis+SQLite in the analyze handler (lines 182-184).

## Implementation Tasks (in dependency order)

### Task 1: `data_reader.py` — Unified telemetry read layer (NEW)

**File:** `apps/cortex/src/services/data_reader.py`

Thin wrapper that merges Redis (hot, <48h) + SQLite (cold) into a single sorted, deduplicated list. Replaces the manual merge in `intent_executor.py:182-184` and provides the data source for the orchestrator's context builder.

```python
class DataReader:
    def __init__(self, redis, sqlite)
    def get_readings(since_ms, until_ms, device_id, limit) -> list[MergedReading]
    def get_recent_readings(window_minutes=30, device_id) -> list[MergedReading]
    def extract_metric(readings, metric) -> tuple[list[float], list[int]]

@dataclass
class MergedReading:
    ts: int
    temp: float
    humidity: float
    device_id: str
```

### Task 2: Extend `analysis.py` — Add rate-of-change and trend context (MODIFY)

**File:** `apps/cortex/src/services/analysis.py`

Add two new functions after the existing `analyze_metric()`. These are designed for the decision loop (fast, no anomaly detection overhead).

```python
@dataclass
class TrendContext:
    metric: str
    trend: str           # "rising" | "falling" | "stable"
    rate_of_change: float  # units/min via linear regression
    current_value: float
    mean_30m: float
    std_dev_30m: float
    min_30m: float
    max_30m: float

def calculate_rate_of_change(values, timestamps) -> float
    # Linear regression slope in units/min. Positive = rising.

def build_trend_context(metric, values, timestamps) -> TrendContext | None
    # Uses calculate_stats() + calculate_rate_of_change().
    # Trend determined by rate relative to noise floor (std_dev * 0.1).
```

### Task 3: `cortex_memory.py` — Baseline tracking (NEW)

**File:** `apps/cortex/src/services/cortex_memory.py`

Adds a `cortex_baselines` table to the existing SQLite DB. Per-device, per-sensor, per-hour-of-day baselines using Welford's online algorithm for running mean/variance.

```python
class CortexMemory:
    def __init__(self, sqlite)  # creates table if not exists
    def update_baseline(device_id, sensor, hour_of_day, value) -> None
        # Incremental update via Welford's algorithm
    def get_baseline(device_id, sensor, hour_of_day) -> Baseline | None
    def get_baseline_deviation(device_id, sensor, hour, current_value) -> float | None
        # Returns sigma deviation. None if < 10 samples.

@dataclass
class Baseline:
    device_id: str
    sensor: str
    hour_of_day: int  # 0-23
    avg_value: float
    std_dev: float
    sample_count: int
```

**Table schema:**
```sql
CREATE TABLE cortex_baselines (
    device_id TEXT NOT NULL,
    sensor TEXT NOT NULL,
    hour_of_day INTEGER NOT NULL,
    avg_value REAL DEFAULT 0,
    std_dev REAL DEFAULT 0,
    sample_count INTEGER DEFAULT 0,
    sum_values REAL DEFAULT 0,
    sum_squares REAL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (device_id, sensor, hour_of_day)
);
```

### Task 4: Evolve `decision_engine.py` — Trend + time-of-day conditions (MODIFY)

**File:** `apps/cortex/src/services/decision_engine.py`

**Extend `RuleCondition`** (line 17):
```python
trend: str | None = None            # "rising" | "falling" | "stable"
trend_window_minutes: int = 30
time_of_day: dict | None = None     # {"after": "08:00", "before": "22:00"}
```

**Modify `from_yaml()`** (line 74-78): Parse the three new fields.

**Modify `evaluate()`** (line 95): Accept optional `context: dict | None = None`. After the existing `condition_met` check (line 116), add:
- Trend check: if `rule.condition.trend` is set and context has trend data for this sensor, verify trend matches
- Time-of-day check: if `rule.condition.time_of_day` is set, verify current time is within range

**Add `_check_time_of_day()`**: Handles both normal ranges (08:00-22:00) and overnight ranges (22:00-06:00).

### Task 5: Wire into orchestrator (`main.py`) — Context building with cache (MODIFY)

**File:** `apps/cortex/src/main.py`

The highest-impact change. The orchestrator must:

1. **Initialize** `DataReader` and `CortexMemory` after API services are ready (after `_wait_for_api()`, ~line 101)

2. **Build context** (cached 30s) before rule evaluation:
   ```python
   def _build_context(self, device_id) -> dict | None:
       # Cache check (30s TTL)
       # Fetch 30min of readings via DataReader
       # Build TrendContext for temp1 and hum1 via build_trend_context()
       # Fetch baselines for current hour via CortexMemory
       # Return {"trends": {...}, "baselines": {...}}
   ```

3. **Pass context** to `engine.evaluate(telemetry, context)`

4. **Update baselines** after each telemetry message (cheap, incremental):
   ```python
   def _update_baselines(self, telemetry):
       # For each reading (temp1, hum1), call memory.update_baseline()
   ```

5. **Enrich LLM escalation context**: When escalating, include trends and baselines in the context dict passed to `ollama.analyze_async()`

### Task 6: Enrich LLM prompts (`ollama_client.py`) (MODIFY)

**File:** `apps/cortex/src/services/ollama_client.py`

**Modify `_build_prompt()`** (line 106-148): Add trend data and baseline comparisons when available in the context dict. The context dict will carry `_trends` and `_baselines` keys (prefixed with `_` to distinguish from device state keys).

New prompt includes:
```
Trend analysis (last 30 minutes):
  - temp1: rising (+0.3°C/min), 30min avg: 24.2
  - hum1: stable (+0.01%/min), 30min avg: 55.1

Baseline comparison (vs normal for this hour):
  - temp1: baseline=22.1, deviation=+1.8σ (unusual, 240 samples)
  - hum1: baseline=54.3, deviation=+0.2σ (normal, 240 samples)
```

### Task 7: Add trend-aware rules (`rules.yaml`) (MODIFY)

**File:** `apps/cortex/config/rules.yaml`

Add two new rules demonstrating Phase 1 capabilities:

```yaml
- name: rising_temp_preemptive
  condition:
    sensor: temp1
    operator: ">"
    threshold: 22
    trend: rising
    trend_window_minutes: 15
    time_of_day: { after: "08:00", before: "22:00" }
    duration_seconds: 30
  action:
    target: relay1
    action: set
    value: true
    reason: "Temperature rising during daytime — preemptive cooling"

- name: falling_temp_save_energy
  condition:
    sensor: temp1
    operator: "<"
    threshold: 23
    trend: falling
    duration_seconds: 60
  action:
    target: relay1
    action: set
    value: false
    reason: "Temperature falling below comfort zone — energy saving"
```

### Task 8: Update `intent_executor.py` to use `DataReader` (MODIFY)

**File:** `apps/cortex/src/services/intent_executor.py`

Replace the manual Redis+SQLite merge in the analyze handler (lines 182-184) with `DataReader`. Add optional `data_reader` parameter to `execute_intent()`.

### Task 9: Wire `DataReader` into route creation (MODIFY)

**Files:** `apps/cortex/src/voice_api.py`, `apps/cortex/src/api/chat.py`, `apps/cortex/src/api/voice.py`

Create `DataReader` in `_mount_routes()` and pass to chat/voice routers, which pass to `execute_intent()`.

## Files Summary

| File | Action | Risk |
|------|--------|------|
| `src/services/data_reader.py` | NEW | Low — pure new module |
| `src/services/analysis.py` | MODIFY — add 2 functions + 1 dataclass | Low — additive |
| `src/services/cortex_memory.py` | NEW | Low — pure new module |
| `src/services/decision_engine.py` | MODIFY — extend RuleCondition, modify evaluate() | Medium — core logic |
| `src/main.py` | MODIFY — context building, baseline updates | High — integration point |
| `src/services/ollama_client.py` | MODIFY — enrich _build_prompt() | Medium — prompt change |
| `config/rules.yaml` | MODIFY — add 2 rules | Low |
| `src/services/intent_executor.py` | MODIFY — use DataReader | Low — backward compatible |
| `src/voice_api.py` | MODIFY — create DataReader, pass to routes | Low |
| `src/api/chat.py` | MODIFY — accept data_reader param | Low |
| `src/api/voice.py` | MODIFY — accept data_reader param | Low |

## Verification

1. **Start services:** `docker compose up -d mosquitto redis` + `python -m src.main`
2. **Publish test telemetry:** Multiple readings over 2-3 minutes to build trend data
3. **Check context building:** Add debug log in `_build_context()` — should show trend direction and rate
4. **Verify baseline accumulation:** After ~10 readings, query `cortex_baselines` table directly
5. **Test trend rules:** Publish a rising temperature sequence → verify `rising_temp_preemptive` fires
6. **Test LLM prompt:** Trigger LLM escalation → check logs for enriched prompt with trends/baselines
7. **Test chat analysis:** `POST /api/chat {"message": "analyze temperature"}` → should still work (DataReader integration)
8. **Test all existing endpoints:** Ensure no regressions on `/api/devices`, `/api/history`, `/api/commands`, etc.
