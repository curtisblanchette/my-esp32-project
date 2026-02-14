# Mycelium + Cortex: Cybernetic Evolution Plan

## Context

The project currently has a reactive automation system: threshold rules fire when sensor values cross static limits. The vision is to evolve into a **cybernetic feedback loop** where two abstractions drive the system:

- **Mycelium** (nervous system) — devices, sensors, actuators. They publish telemetry, expose capabilities, execute commands.
- **Cortex** (brain) — LLM + rules engine. Reads history, sees patterns, predicts, decides, commands.

The loop: `devices → history → interpretation → decision → command → devices`

Today's biggest gap: **Cortex is blind to history.** It only sees the current MQTT telemetry message. The decision engine compares single values against static thresholds. The LLM receives "Historical context" that's really just the last-seen value per sensor.

**Architecture decision: Drop Node.js, consolidate into Python.**

The Node.js API (`apps/api`) currently handles MQTT ingestion, Redis/SQLite storage, WebSocket broadcasting, and REST endpoints. But Cortex (Python) duplicates the MQTT client and needs all the same data. Rather than maintain two MQTT clients in two languages with inter-service HTTP calls, we consolidate everything into a single Python backend.

Cortex becomes the entire backend: MQTT, storage, WebSocket, REST API, voice, decision engine. The React dashboard (`apps/web`) is unchanged — it just points to the Python server instead of Node.js.

```
BEFORE:                              AFTER:
ESP32 → MQTT → Node.js (store/WS)   ESP32 → MQTT → Cortex (Python)
              → Cortex (duplicate)              ├─ Redis/SQLite (store)
React → Node.js API                            ├─ WebSocket (broadcast)
React → proxy → Cortex (voice)                 ├─ REST API (FastAPI)
                                               ├─ Decision engine
                                    React ────► └─ Voice STT/TTS
```

---

## Phase 0: Consolidate Node.js into Cortex

Port all `apps/api` functionality into `apps/cortex` using FastAPI, then remove `apps/api`.

### 0a. Storage layer
Create `apps/cortex/src/services/redis_client.py` — port from `apps/api/src/lib/redis.ts`:
- `store_reading(reading)` — store with 48hr TTL
- `get_readings_in_range(since_ms, until_ms, device_id?)` — range query via SCAN

Create `apps/cortex/src/services/sqlite_client.py` — port from `apps/api/src/lib/sqlite.ts`:
- Tables: `sensor_readings`, `commands`, `events`, `devices`
- All query functions: `insert_reading()`, `query_history()`, `query_commands()`, `query_events()`
- Device registry: `upsert_device()`, `get_devices()`, `update_device_order()`
- Command lifecycle: `insert_command()`, `update_command_status()`

### 0b. MQTT client enhancement
Enhance existing `apps/cortex/src/services/mqtt_client.py`:
- Add telemetry → Redis + SQLite storage (ported from `mqttTelemetry.ts`)
- Add command publishing with correlation ID + SQLite audit
- Add WebSocket broadcast on telemetry/device/relay events
- Remove legacy `/device/+/telemetry` subscription

### 0c. WebSocket server
Create `apps/cortex/src/services/websocket_server.py` — port from `apps/api/src/services/websocket.ts`:
- FastAPI native WebSocket endpoint at `/ws`
- Broadcast types: `latest`, `devices`, `relays`, `command`, `event`
- On connect: send current state (latest readings, devices, relays, events, commands)

### 0d. REST API routes
Create `apps/cortex/src/api/` — port from `apps/api/src/routes/`:
- `telemetry.py` — `GET /api/latest`, `GET /api/history` (merge Redis + SQLite)
- `devices.py` — `GET /api/devices`, `GET /api/devices/:id`, `PUT /api/devices/order`
- `relays.py` — `GET/POST/PATCH/DELETE /api/devices/:id/relays/:relayId`
- `commands.py` — `GET /api/commands`
- `events.py` — `GET /api/events`
- `chat.py` — `POST /api/chat`, `POST /api/chat/stream` (SSE), `GET /api/chat/health`
- `voice.py` — move existing voice endpoints, remove proxy layer (direct access now)

### 0e. Intent execution
Create `apps/cortex/src/services/intent_executor.py` — port from `executeIntent.ts`:
- Shared handler for chat + voice intents
- Intent types: `command`, `query`, `history`, `analyze`, `none`
- Commands publish via MQTT + store in SQLite

### 0f. Analysis utilities
Create `apps/cortex/src/services/analysis.py` — port from `analysis.ts`:
- `calculate_stats()`, `detect_anomalies()`, `detect_trend()`
- `analyze_sensor_data()`, `format_analysis_reply()`
- Timeframe parsing (`1h`, `6h`, `12h`, `24h`, `7d`, `30d`)

### 0g. Background jobs
Add to `apps/cortex/src/main.py` as asyncio tasks:
- Aggregation job (Redis → SQLite bucketing, every 10 min)
- Command expiration job (expire pending commands past TTL)

### 0h. Ollama chat integration
Enhance existing `apps/cortex/src/services/ollama_client.py`:
- Add chat intent interpretation (port `interpretMessage()` / `interpretMessageStream()` from `ollama.ts`)
- Add system prompt with device context and intent schemas (port from `systemPrompt.ts`)
- Keep existing decision engine escalation support

### 0i. Update web proxy + remove Node.js
- Update `apps/web/vite.config.ts` proxy target to point to Python (port 8000)
- Remove `apps/api/` directory entirely
- Remove Node.js dependencies from root `package.json` (keep only web + turborepo)
- Update `docker-compose.yml`, `tools/start.sh`, `tools/stop.sh`
- Update `CLAUDE.md`, `README.md`

### Files changed (Phase 0):
- **New:** `apps/cortex/src/services/redis_client.py`, `sqlite_client.py`, `websocket_server.py`, `intent_executor.py`, `analysis.py`
- **New:** `apps/cortex/src/api/` directory with route modules
- **Enhanced:** `apps/cortex/src/services/mqtt_client.py`, `ollama_client.py`
- **Enhanced:** `apps/cortex/src/main.py` (bootstrap all services)
- **Modified:** `apps/web/vite.config.ts`, `docker-compose.yml`, `tools/*.sh`, `CLAUDE.md`, `README.md`
- **Deleted:** `apps/api/` (entire directory)

---

## Phase 1: Vision Document + Cortex Data Access

### 1a. Fill `docs/mycelium-cortex-differentiator.md`
Write the vision document articulating the Mycelium/Cortex architecture, cybernetic loop, and how this project differs from conventional HA platforms.

### 1b. Trend Analyzer
Create `apps/cortex/src/services/trend_analyzer.py` — extend the analysis module from Phase 0 with trend-specific features.

```
Functions:
  calculate_stats(values) → Stats
  detect_trend(values) → "rising" | "falling" | "stable"
  calculate_rate_of_change(values, timestamps) → float (units/min)
  detect_anomalies(values, timestamps) → list[Anomaly]
```

### 1d. Cortex Memory (persistent storage)
Create `apps/cortex/src/services/memory.py` — separate SQLite DB at `apps/cortex/data/cortex.sqlite`.

```
Tables:
  baselines — device_id, sensor_id, hour_of_day, day_of_week, avg_value, stddev, sample_count
  outcomes  — correlation_id, device_id, target, pre_snapshot, post_snapshots, effectiveness
  patterns  — device_id, sensor_id, pattern_type, description, confidence, parameters
```

### 1d. Evolve the Decision Engine
Modify `apps/cortex/src/services/decision_engine.py`:
- Add `trend`, `trend_window_minutes`, and `time_of_day` fields to `RuleCondition`
- `evaluate()` accepts an optional `context` dict with trends/baselines/time
- Parse new condition fields from YAML
- Check trend + time-of-day when present

### 1e. Wire into Orchestrator
Modify `apps/cortex/src/main.py`:
- Instantiate `TrendAnalyzer`, `CortexMemory` in `__init__`
- Build context before rule evaluation (cached 30s):
  - Read 30min history from SQLite (direct, in-process — no API call)
  - Compute trends and rate-of-change
  - Load baselines from memory
- Pass context to `engine.evaluate(telemetry, context)`
- Update baselines after each telemetry message

### 1f. Enrich LLM prompts
Modify `apps/cortex/src/services/ollama_client.py`:
- Replace dummy "Historical context" with real trends, rates of change, and baseline comparisons

### 1g. Add trend-aware rules
Update `apps/cortex/config/rules.yaml` with rules like:
```yaml
- name: rising_temp_preemptive
  condition:
    sensor: temp1
    operator: ">"
    threshold: 22
    trend: rising
    trend_window_minutes: 15
    time_of_day: { after: "08:00", before: "22:00" }
  action:
    target: relay1
    action: set
    value: true
    reason: "Temperature rising during daytime"
```

### Files changed (Phase 1):
- **New:** `apps/cortex/src/services/trend_analyzer.py`, `apps/cortex/src/services/memory.py`
- **New:** `apps/cortex/data/` directory
- **Modified:** `apps/cortex/src/services/decision_engine.py`, `apps/cortex/src/main.py`, `apps/cortex/src/services/ollama_client.py`, `apps/cortex/src/config.py`, `apps/cortex/config/rules.yaml`
- **Modified:** `docs/mycelium-cortex-differentiator.md`

---

## Phase 2: Outcome Tracking — Close the Feedback Loop

Track what happens after commands fire. Did turning on the fan actually lower the temperature?

### 2a. Data models
Create `apps/cortex/src/models/pattern.py` — dataclasses for `Baseline`, `OutcomeRecord`, `Pattern`.

### 2b. Outcome Tracker
Create `apps/cortex/src/services/outcome_tracker.py`:

The Outcome Tracker closes the cybernetic loop by correlating **commands** with their **actual effect on sensor readings**. This is the mechanism by which Cortex learns whether its decisions are working.

**Lifecycle of a tracked outcome:**

```
1. COMMAND FIRES (e.g., relay1 ON, reason: "temp > 25")
   │
   ├─ OutcomeTracker.track_command(command, telemetry)
   │   → Snapshots current sensor state as pre_snapshot:
   │     { temp1: 26.3, hum1: 58.2, ts: 1707000000 }
   │   → Stores as PendingOutcome in memory with check_intervals: [60, 300, 600]
   │
2. DEVICE ACKS (ack arrives via MQTT)
   │
   ├─ OutcomeTracker.handle_ack(correlation_id, ack)
   │   → Marks outcome as "acked" or "failed"
   │   → If failed, outcome is scored immediately as effectiveness: 0.0
   │
3. CHECK INTERVALS (called every 30s from main loop)
   │
   ├─ OutcomeTracker.check_outcomes()
   │   → For each pending outcome past its next check interval:
   │
   │   At +1 minute:
   │     → Read current sensor values via DataReader
   │     → Store as post_snapshot_1m: { temp1: 25.8, hum1: 57.9 }
   │     → Early signal: temp dropped 0.5°C (good sign)
   │
   │   At +5 minutes:
   │     → Read again → post_snapshot_5m: { temp1: 24.1, hum1: 56.3 }
   │     → Meaningful signal: temp dropped 2.2°C from pre-command
   │
   │   At +10 minutes:
   │     → Final read → post_snapshot_10m: { temp1: 23.5, hum1: 55.8 }
   │     → All snapshots collected, compute final score
   │
4. SCORE EFFECTIVENESS
   │
   ├─ _score_outcome(pre_snapshot, post_snapshots, command)
   │   → Determines which sensor is the "target metric" based on the command's reason
   │     (e.g., reason contains "temp" → track temp1)
   │   → Calculates delta: pre_value - post_value at each interval
   │   → Scores from -1.0 to 1.0:
   │       +1.0 = strong improvement (sensor moved significantly in desired direction)
   │        0.0 = no effect (sensor unchanged)
   │       -1.0 = made it worse (sensor moved in wrong direction)
   │   → Weighted toward the 5min snapshot (most useful signal)
   │
5. STORE & LEARN
   │
   └─ memory.store_outcome(OutcomeRecord)
       → Persisted to cortex.sqlite outcomes table
       → Queryable by: device_id, target, action, time range
       → Aggregatable: "relay1 ON averages -1.8°C over 5min across 12 samples"
```

**What the accumulated outcomes enable:**
- LLM receives effectiveness data: "turning relay1 ON typically drops temp by 1.8°C over 5 minutes"
- Decision engine can prefer more effective actions over less effective ones
- Rule advisor (Phase 4) can identify rules that consistently score poorly
- Anomaly detection: if a command that usually scores 0.8 suddenly scores 0.0, something changed

**Key design decisions:**
- Outcomes are per-command, not per-rule (a rule may fire many commands over time)
- The "target metric" is inferred from the command's reason string (simple heuristic: if reason mentions "temp", track temperature)
- Check intervals are configurable but default to [60, 300, 600] seconds
- Pending outcomes are stored in-memory; completed outcomes persist to SQLite
- If the device goes offline during tracking, the outcome is scored as 0.0 (unknown)

### 2c. Wire into Orchestrator
Modify `apps/cortex/src/main.py`:
- Call `outcome_tracker.track_command(command, current_telemetry)` in `_execute_command()`
- Call `outcome_tracker.check_outcomes()` periodically (every 30s from main loop)
- Call `outcome_tracker.handle_ack(ack)` in `_handle_ack()`

### 2d. Expose analysis endpoint
Add `GET /api/analysis?deviceId=X&sinceMs=Y&metric=temperature` route in `apps/cortex/src/api/telemetry.py` using the analysis module.

### 2e. Feed effectiveness into LLM
Modify `apps/cortex/src/services/ollama_client.py` to include outcome data:
```
Past command effectiveness:
- relay1 ON when temp > 25: avg -1.8C over 5min (85% effective, 12 samples)
```

### Files changed (Phase 2):
- **New:** `apps/cortex/src/services/outcome_tracker.py`, `apps/cortex/src/models/pattern.py`
- **Modified:** `apps/cortex/src/main.py`, `apps/cortex/src/services/ollama_client.py`, `apps/cortex/src/api/telemetry.py`

---

## Phase 3: Forecasting — Predict, Don't Just React

### 3a. Forecaster module
Create `apps/cortex/src/services/forecaster.py`:

The Forecaster answers one question: **"Where is this sensor heading?"** It uses recent history (provided by TrendAnalyzer via DataReader) to project future values.

**Methods:**

```python
linear_forecast(values, timestamps, horizon_minutes) → float
    # Fits y = mx + b on the last N data points
    # Returns predicted value at now + horizon
    # Best for: temperature (thermal inertia makes it relatively linear short-term)

will_exceed(values, timestamps, threshold, within_minutes) → (bool, float)
    # Uses linear_forecast to check if threshold will be crossed
    # Returns (will_it_happen, estimated_minutes_until_breach)
    # Returns (False, inf) if trend is moving away from threshold

ewma_forecast(values, alpha=0.3) → float
    # Exponentially weighted moving average — smooths noise
    # More recent values weighted heavier (alpha controls decay)
    # Best for: humidity (more volatile than temp, benefits from smoothing)

baseline_deviation(current, baseline) → float
    # How far is the current value from what's normal for this hour/day?
    # Uses baselines from Cortex memory (Phase 1)
    # Returns standard deviations from baseline mean
```

### 3b. Forecast conditions in rules

Extend `RuleCondition` with new fields:
```python
forecast: str | None           # "will_exceed" or "will_drop_below"
forecast_threshold: float      # the value to forecast against
forecast_within_minutes: float # time horizon for prediction
baseline_deviation: float      # trigger when N stddevs from baseline
```

**How forecast rules differ from threshold rules:**

```
THRESHOLD RULE (today):
  "Is temp > 28 right now?"
  → Only fires AFTER the problem exists
  → Reactive: damage may already be done (e.g., plant stress, mold forming)

FORECAST RULE (Phase 3):
  "Will temp exceed 28 within the next 10 minutes?"
  → Fires BEFORE the problem exists
  → Preemptive: intervene while there's still time

BASELINE RULE (Phase 3):
  "Is temp 2+ standard deviations above what's normal for this hour?"
  → Fires on abnormality, not absolute values
  → Adaptive: 22°C at 3am is abnormal; 22°C at 2pm is fine
```

**Evaluation flow in the Decision Engine:**

```
telemetry arrives (e.g., temp1 = 25.4°C)
    │
    ├─ Context already built (cached 30s, from Phase 1):
    │   context.forecast.temp1 = {
    │     predicted_10m: 27.8,       ← linear forecast
    │     will_exceed_28: true,      ← breach prediction
    │     minutes_until_28: 12.3,    ← time to breach
    │     ewma: 25.2,               ← smoothed current
    │     baseline_for_hour: 23.1,   ← what's normal now
    │     baseline_stddev: 1.4,      ← normal variation
    │     deviation: 1.64            ← (25.4 - 23.1) / 1.4 = 1.64σ
    │   }
    │
    ├─ Rule: "preemptive_cooling"
    │   condition:
    │     sensor: temp1
    │     forecast: will_exceed
    │     forecast_threshold: 28
    │     forecast_within_minutes: 15
    │
    │   Decision Engine checks:
    │   1. Does context have a forecast for temp1? ✓
    │   2. forecast says will_exceed_28 = true? ✓
    │   3. minutes_until_28 (12.3) ≤ forecast_within_minutes (15)? ✓
    │   4. Duration requirement met? (instant for forecasts, or configurable)
    │   5. Cooldown not active? ✓
    │   → FIRES: relay1 ON, reason: "Predicted temp will exceed 28C in ~12 min"
    │
    ├─ Rule: "abnormal_nighttime_heat"
    │   condition:
    │     sensor: temp1
    │     baseline_deviation: 2.0
    │     time_of_day: { after: "22:00", before: "06:00" }
    │
    │   Decision Engine checks:
    │   1. Current deviation (1.64σ) ≥ 2.0σ? ✗
    │   → Does NOT fire (not abnormal enough yet)
```

### 3c. Predictive rules
```yaml
  # Preemptive — act before threshold is reached
  - name: preemptive_cooling
    description: "Start fan before temperature reaches critical threshold"
    condition:
      sensor: temp1
      forecast: will_exceed
      forecast_threshold: 28
      forecast_within_minutes: 15
    action:
      target: relay1
      action: set
      value: true
      reason: "Predicted temp will exceed 28C within 15 minutes"

  # Baseline deviation — act on abnormality, not absolutes
  - name: abnormal_nighttime_heat
    description: "Alert when temperature is unusually high for nighttime"
    condition:
      sensor: temp1
      baseline_deviation: 2.0
      time_of_day: { after: "22:00", before: "06:00" }
    action:
      target: relay1
      action: set
      value: true
      reason: "Temperature abnormally high for this time of night"

  # Combined — forecast + trend + time for maximum intelligence
  - name: morning_humidity_preempt
    description: "Preempt morning humidity spike based on historical pattern"
    condition:
      sensor: hum1
      forecast: will_exceed
      forecast_threshold: 65
      forecast_within_minutes: 20
      trend: rising
      time_of_day: { after: "06:00", before: "10:00" }
    action:
      target: relay1
      action: set
      value: true
      reason: "Morning humidity rising toward 65%, preemptive ventilation"
```

**Key design considerations:**
- Forecast accuracy degrades beyond ~15 minutes for linear regression. Short horizons are more reliable.
- Humidity is noisier than temperature — EWMA smoothing before forecasting improves reliability.
- Baseline deviation rules need ~48 hours of data to be meaningful (baselines must accumulate first).
- Forecast rules should have longer cooldowns (e.g., 5 minutes) to avoid re-triggering on every telemetry cycle while the forecast holds.
- The Forecaster logs its predictions vs actuals so forecast accuracy can be tracked over time (feeds into Phase 4 learning).

### Files changed (Phase 3):
- **New:** `apps/cortex/src/services/forecaster.py`
- **Modified:** `apps/cortex/src/services/decision_engine.py`, `apps/cortex/src/main.py`, `apps/cortex/config/rules.yaml`

---

## Phase 4: Adaptive Learning — Cortex Evolves Its Own Rules

### 4a. Rule Advisor
Create `apps/cortex/src/services/rule_advisor.py`:
- Analyzes outcome data periodically (every 6 hours)
- Uses LLM to suggest threshold/timing adjustments
- High-confidence adjustments auto-apply; others require approval

### 4b. Cortex API endpoints
Extend `apps/cortex/src/api.py`:
```
GET  /cortex/status          — system intelligence overview
GET  /cortex/outcomes         — recent outcome records
GET  /cortex/baselines/:id    — learned baselines
GET  /cortex/adjustments      — pending rule suggestions
POST /cortex/adjustments/:id  — approve/reject suggestion
```

### Files changed (Phase 4):
- **New:** `apps/cortex/src/services/rule_advisor.py`
- **Modified:** `apps/cortex/src/api.py`, `apps/cortex/src/main.py`

---

## Phase 5: Multi-Device Coordination (Stretch)

### 5a. Device Coordinator
Create `apps/cortex/src/services/coordinator.py`:
- Cross-device correlation detection
- Rules spanning multiple devices (e.g., "if ANY device temp > 30, turn on ALL fans")

---

## Cybernetic Loop (Complete)

```
    MYCELIUM                                    CORTEX
┌──────────────┐                     ┌─────────────────────────┐
│  ESP32       │   telemetry         │  TrendAnalyzer          │
│  sensors     │ ──── MQTT ────────► │  Forecaster             │
│  actuators   │                     │  Memory (baselines)     │
│              │   command           │         │                │
│              │ ◄─── MQTT ──────── │  DecisionEngine         │
└──────────────┘                     │  (rules + trend +       │
       │                             │   forecast + time)      │
       │  ack                        │         │                │
       └──────── MQTT ─────────────► │  OutcomeTracker         │
                                     │  (effectiveness →       │
                                     │   memory → better       │
                                     │   decisions next time)  │
                                     │         │                │
                                     │  OllamaClient (LLM)    │
                                     │  RuleAdvisor            │
                                     └─────────────────────────┘
```

---

## Verification

After each phase:
1. Start Cortex: `python -m src.main` (from `apps/cortex/`)
2. Start Web: `npm run dev` (from `apps/web/`)
3. **Phase 0:** Dashboard loads, shows live telemetry via WebSocket, relay controls work, chat works
4. **Phase 1:** Trend data appears in logs and LLM prompts
5. **Phase 2:** Fire a command, verify outcome records appear in `cortex.sqlite`
6. **Phase 3:** Simulate rising temperature, verify preemptive rule triggers before threshold
7. **Phase 4:** Check `/cortex/adjustments` endpoint returns suggestions after data accumulates

---

## Implementation Order

Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5

Phase 0 is the consolidation — drop Node.js, make Cortex the sole backend. Everything after builds on that unified foundation.
