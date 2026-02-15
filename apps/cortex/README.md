# Cortex

The autonomous decision engine for Mycelium. Cortex ingests real-time sensor telemetry via MQTT, evaluates automation rules enriched with trend analysis and predictive forecasting, tracks command effectiveness, and adapts its own rules over time using LLM-powered analysis. It also provides voice processing (STT/TTS) and natural-language chat for the dashboard.

## Architecture

```
ESP32 (MicroPython)
  │
  ▼ MQTT telemetry
Cortex ─────────────────────────────────────────────────────────
  │
  ├─ Ingest ──→ Redis (HOT, 48h TTL) + SQLite (COLD, aggregated)
  │
  ├─ Context ─→ DataReader (merge hot+cold)
  │             TrendContext (linear regression, rate-of-change)
  │             CortexMemory (Welford's online baselines)
  │             Forecaster (linear projection, EWMA smoothing)
  │             OutcomeTracker (command effectiveness scores)
  │
  ├─ Decide ──→ Rules Engine ──→ MQTT commands ──→ ESP32
  │             │  threshold + duration guards + cooldown
  │             │  trend / time-of-day / forecast / baseline conditions
  │             │  cross-device scope (any / all / specific device)
  │             │
  │             └─ LLM escalation (rapid change, conflicts, unknowns)
  │
  ├─ Learn ───→ Rule Advisor (6h cycle)
  │             LLM analyzes outcomes + baselines + observations
  │             High-confidence threshold tweaks auto-apply
  │             Lower-confidence suggestions → manual approval
  │
  └─ Serve ───→ FastAPI REST + WebSocket + Voice (STT/TTS) + Chat
```

### Five-Phase Cybernetic Loop

| Phase | Capability | Key Module |
|-------|-----------|------------|
| 1 | Trend context + hourly baselines | `analysis.py`, `cortex_memory.py` |
| 2 | Command outcome tracking & scoring | `outcome_tracker.py` |
| 3 | Predictive forecasting + breach detection | `forecaster.py` |
| 4 | Adaptive rule learning via LLM | `rule_advisor.py` |
| 5 | Multi-device coordination | `coordinator.py` |

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment config
cp .env.example .env

# Download voice models (optional — only needed for STT/TTS)
mkdir -p models
wget -q https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip \
  && unzip -q vosk-model-small-en-us-0.15.zip -d models \
  && rm vosk-model-small-en-us-0.15.zip

curl -L -o models/kokoro-v1.0.onnx \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx

curl -L -o models/voices-v1.0.bin \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

# Run (MQTT loop + HTTP API + rules engine + voice)
python -m src.main
```

Or run the API server only (no MQTT/rules engine):

```bash
uvicorn src.voice_api:app --host 0.0.0.0 --port 8000 --reload
```

Or with Docker:

```bash
docker build -t cortex .
docker run -p 8000:8000 cortex
```

> The Dockerfile downloads all voice models during the build.

## How It Works

### Decision Engine

Cortex subscribes to MQTT telemetry and evaluates rules defined in `config/rules.yaml`:

1. **Rule evaluation** — checks sensor readings against conditions with duration guards (condition must hold for N seconds) and cooldown timers (60s default) to prevent false positives and rapid toggling
2. **Context enrichment** — every evaluation includes trend direction, rate-of-change, learned baselines, forecasted values, and past command effectiveness
3. **Command execution** — publishes MQTT commands to devices when rules trigger; tracks outcomes at 1m/5m/10m intervals to score effectiveness
4. **LLM escalation** — routes anomalies (rapid changes, conflicting rules, unknown patterns) to Ollama for deeper analysis when rules alone aren't sufficient
5. **Adaptive learning** — every 6 hours, the Rule Advisor uses LLM analysis of outcomes, baselines, and human observations to suggest threshold and timing adjustments

### Chat & Voice

Both text chat and voice commands flow through a shared `execute_intent()` function:

```
Text Chat ──→ POST /chat/stream ──→ Ollama (intent parsing) ──→ execute_intent()
Voice     ──→ POST /voice/command ──→ Vosk STT ──→ Ollama ──→ execute_intent()
```

Intents: `command` (relay control), `query` (latest reading), `history` (time series), `analyze` (stats + anomalies), `none` (passthrough LLM reply).

### Voice Processing

- **Vosk** — offline speech-to-text (~40MB model, 16kHz mono)
- **Kokoro** — ONNX-based text-to-speech with 20 voice options (24kHz WAV output)

Text normalization handles numbers, times, currency, percentages, ordinals, emojis, and markdown before synthesis. Long text is chunked to stay within Kokoro's 510 phoneme limit.

### Storage Strategy

| Layer | Store | Purpose |
|-------|-------|---------|
| HOT | Redis | Raw readings with 48-hour TTL for fast recent queries |
| COLD | SQLite | Aggregated historical data, device registry, command history |
| Unified | DataReader | Merges Redis + SQLite, deduplicates by timestamp |

SQLite tables: `telemetry`, `devices`, `actuators`, `commands`, `events`, `cortex_baselines`, `cortex_outcomes`, `cortex_suggestions`

A background job aggregates Redis → SQLite every 5 minutes.

## Rules Configuration

Rules live in `config/rules.yaml`. The engine supports layered conditions that can be combined:

```yaml
rules:
  # Basic threshold rule
  - name: high_temp_alert
    condition:
      sensor: temp1
      operator: ">"           # supports: > < >= <= == !=
      threshold: 25
      duration_seconds: 15    # must hold this long before firing
    action:
      target: relay1
      action: set
      value: true
      reason: "Temperature exceeded threshold"

  # Phase 1: Trend + time-of-day conditions
  - name: rising_temp_preemptive
    condition:
      sensor: temp1
      operator: ">"
      threshold: 22
      trend: rising           # rising | falling | stable
      trend_window_minutes: 15
      time_of_day:
        after: "08:00"
        before: "22:00"
      duration_seconds: 30
    action:
      target: relay1
      action: set
      value: true
      reason: "Temperature rising during daytime — preemptive cooling"

  # Phase 3: Forecast + baseline deviation
  - name: preemptive_cooling
    condition:
      sensor: temp1
      operator: ">="
      threshold: 0
      forecast: will_exceed        # will_exceed | will_drop_below
      forecast_threshold: 28
      forecast_within_minutes: 15
    action:
      target: relay1
      action: set
      value: true
      reason: "Predicted temp will exceed 28°C within 15 minutes"

  - name: abnormal_nighttime_heat
    condition:
      sensor: temp1
      operator: ">="
      threshold: 0
      baseline_deviation: 2.0      # N standard deviations from hourly baseline
      time_of_day:
        after: "22:00"
        before: "06:00"
      duration_seconds: 60
    action:
      target: relay1
      action: set
      value: true
      reason: "Temperature abnormally high for this time of night"

  # Phase 5: Cross-device coordination
  - name: any_device_overheat
    condition:
      sensor: temp1
      operator: ">"
      threshold: 30
      scope: any                   # self | any | all | <device_id>
      duration_seconds: 30
    action:
      target: relay1
      action: set
      value: true
      target_scope: all            # self | all | <device_id>
      reason: "Cross-device overheat — activating all fans"

llm:
  enabled: true
  model: "phi3:mini"
  escalation_triggers:
    - rapid_change: 5
    - conflicting_rules: true
    - unknown_pattern: true
```

### Condition Reference

| Field | Phase | Values | Description |
|-------|-------|--------|-------------|
| `sensor` | — | string | Sensor ID to evaluate |
| `operator` | — | `> < >= <= == !=` | Comparison operator |
| `threshold` | — | number | Value to compare against |
| `duration_seconds` | — | number | Seconds the condition must hold |
| `trend` | 1 | `rising` `falling` `stable` | Rate-of-change direction |
| `trend_window_minutes` | 1 | number | Window for trend calculation |
| `time_of_day` | 1 | `{after, before}` | Time range constraint (HH:MM) |
| `forecast` | 3 | `will_exceed` `will_drop_below` | Predictive breach check |
| `forecast_threshold` | 3 | number | Forecasted value to compare |
| `forecast_within_minutes` | 3 | number | Forecast horizon |
| `baseline_deviation` | 3 | number | Std deviations from hourly baseline |
| `scope` | 5 | `self` `any` `all` `<device_id>` | Which devices to read from |

### Action Reference

| Field | Phase | Values | Description |
|-------|-------|--------|-------------|
| `target` | — | string | Actuator ID |
| `action` | — | `set` | Action type |
| `value` | — | boolean | Desired state |
| `reason` | — | string | Human-readable explanation |
| `target_scope` | 5 | `self` `all` `<device_id>` | Which devices to command |

## API Endpoints

### Telemetry & Analysis

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/latest` | Current sensor readings |
| `GET` | `/api/history` | Historical data (`sinceMs`, `untilMs`, `bucketMs`, `deviceId`) |
| `GET` | `/api/outcomes` | Command effectiveness records (`deviceId`, `target`, `sinceMs`, `limit`) |

### Devices

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/devices` | All registered devices |
| `GET` | `/api/devices/:id` | Single device |
| `GET` | `/api/devices/:id/actuators` | Device actuators |
| `PUT` | `/api/devices/order` | Reorder devices (`{order: string[]}`) |

### Relays

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/devices/:deviceId/relays` | Relay states for a device |
| `GET` | `/api/devices/:deviceId/relays/:id` | Single relay state |
| `POST` | `/api/devices/:deviceId/relays/:id` | Control relay (`{state: boolean}`) |
| `PATCH` | `/api/devices/:deviceId/relays/:id` | Rename relay |
| `DELETE` | `/api/devices/:deviceId/relays/:id` | Delete relay |

### Commands & Events

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/commands` | Command history |
| `GET` | `/api/events` | Device event log |
| `POST` | `/api/observations` | Log human observation (`{deviceId, category, notes?}`) |

### Cortex Intelligence

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/cortex/status` | System overview (outcome/baseline/suggestion counts) |
| `GET` | `/api/cortex/baselines/:deviceId` | Learned hourly baselines |
| `GET` | `/api/cortex/adjustments` | Rule suggestions (`?status=pending\|applied\|rejected`) |
| `POST` | `/api/cortex/adjustments/:id` | Approve or reject suggestion (`{action: "approve"\|"reject"}`) |

### Chat

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Process natural language message |
| `POST` | `/api/chat/stream` | Streaming chat response (SSE) |
| `GET` | `/api/chat/health` | Ollama availability check |

### Voice

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/voice/transcribe` | Audio → text (Vosk STT) |
| `POST` | `/api/voice/synthesize` | Text → audio (Kokoro TTS) |
| `POST` | `/api/voice/command` | Full STT → LLM → execute pipeline |

### WebSocket

| Path | Description |
|------|-------------|
| `/ws` | Real-time updates |

Message types: `latest`, `relays`, `devices`, `commands`, `events`, `suggestions`

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health (STT, TTS, LLM availability) |

## MQTT Topics

**Subscriptions:**

| Topic | Direction | Description |
|-------|-----------|-------------|
| `home/+/+/telemetry` | Device → Cortex | Sensor readings |
| `home/+/+/ack` | Device → Cortex | Command acknowledgments |
| `home/_registry/+/birth` | Device → Cortex | Device registration |
| `home/_registry/+/will` | Broker → Cortex | Device offline (LWT) |

**Publications:**

| Topic | Direction | Description |
|-------|-----------|-------------|
| `home/{location}/{deviceId}/command` | Cortex → Device | Actuator commands |

## Background Jobs

| Job | Interval | Description |
|-----|----------|-------------|
| Aggregation | 5 min | Roll up Redis readings → SQLite |
| Command expiration | 30 sec | Mark stale unacknowledged commands as expired |
| Rule Advisor | 6 hours | LLM-powered rule analysis and suggestion generation |

## Testing

```bash
# Unit tests (no external services needed)
pytest tests/ -m "not e2e"

# All tests including e2e (requires Mosquitto + Redis + Cortex running)
pytest tests/ -m e2e

# Verbose output
pytest tests/ -v
```

Test modules cover: analysis, baselines (Welford's), data reader merge, decision engine rules, forecasting, outcome tracking, rule advisor, coordinator, cross-device rules, cortex API routes, and observations.

Shared fixtures in `conftest.py`: `sqlite_db` (in-memory), `mock_redis`, `telemetry_factory`.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_HOST` | `localhost` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | — | Broker auth username |
| `MQTT_PASSWORD` | — | Broker auth password |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `SQLITE_PATH` | `data/telemetry.sqlite` | SQLite database path |
| `SQLITE_JOURNAL_MODE` | `WAL` | SQLite journal mode |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.2:3b` | LLM model for escalation + chat |
| `HTTP_PORT` | `8000` | FastAPI server port |
| `RULES_PATH` | `config/rules.yaml` | Rule definitions file |
| `DEFAULT_DEVICE_ID` | `esp32-1` | Fallback device ID |
| `DEFAULT_LOCATION` | `room1` | Fallback location |
| `VOSK_MODEL_PATH` | `models/vosk-model-small-en-us-0.15` | Vosk STT model directory |
| `KOKORO_MODEL_PATH` | `models/kokoro-v1.0.onnx` | Kokoro TTS model (~326MB) |
| `KOKORO_VOICES_PATH` | `models/voices-v1.0.bin` | Voice embeddings (~27MB) |
| `KOKORO_VOICE` | `af_heart` | TTS voice name |
| `KOKORO_SPEED` | `1.0` | TTS playback speed |
| `KOKORO_LANG` | `en-us` | TTS language code |

<details>
<summary>Available Kokoro voices</summary>

`af_heart` `af_alloy` `af_bella` `af_jessica` `af_nova` `af_sarah` `af_sky` `am_adam` `am_echo` `am_eric` `am_liam` `am_michael` `am_onyx` `bf_alice` `bf_emma` `bf_isabella` `bf_lily` `bm_daniel` `bm_fable` `bm_george` `bm_lewis`

</details>

## Project Structure

```
apps/cortex/
├── config/
│   └── rules.yaml                    # Automation rules
├── data/                             # Runtime data (gitignored)
│   └── telemetry.sqlite
├── models/                           # ML models (gitignored)
│   ├── vosk-model-small-en-us-0.15/
│   ├── kokoro-v1.0.onnx
│   └── voices-v1.0.bin
├── src/
│   ├── main.py                       # Entry point: MQTT + API + rules engine
│   ├── voice_api.py                  # FastAPI app (lifespan, routes, WebSocket)
│   ├── config.py                     # Environment config loader
│   ├── api/                          # REST route handlers
│   │   ├── chat.py                   #   Chat / NLP endpoints
│   │   ├── commands.py               #   Command history
│   │   ├── cortex.py                 #   Intelligence endpoints (Phase 4)
│   │   ├── devices.py                #   Device registry
│   │   ├── events.py                 #   Event log
│   │   ├── observations.py           #   Human observation logging
│   │   ├── relays.py                 #   Actuator control
│   │   ├── telemetry.py              #   Sensor data queries
│   │   └── voice.py                  #   Voice STT/TTS endpoints
│   ├── models/
│   │   ├── telemetry.py              # TelemetryMessage & Reading
│   │   └── command.py                # Command & CommandAck
│   └── services/
│       ├── analysis.py               # Stats, trends, anomaly detection
│       ├── background_jobs.py        # Aggregation, expiration, rule advisor
│       ├── coordinator.py            # Cross-device state provider (Phase 5)
│       ├── cortex_memory.py          # Hourly baseline tracking (Phase 1)
│       ├── data_reader.py            # Unified Redis + SQLite reader
│       ├── decision_engine.py        # Rules engine + LLM escalation
│       ├── forecaster.py             # Linear/EWMA forecasting (Phase 3)
│       ├── intent_executor.py        # Shared chat + voice intent handler
│       ├── mqtt_client.py            # MQTT subscriber/publisher
│       ├── ollama_client.py          # LLM client (intents, analysis, rules)
│       ├── outcome_tracker.py        # Command effectiveness scoring (Phase 2)
│       ├── redis_client.py           # Hot storage (48h TTL)
│       ├── rule_advisor.py           # Adaptive rule learning (Phase 4)
│       ├── shared.py                 # Shared service registry
│       ├── sqlite_client.py          # Cold storage + schema + migrations
│       ├── voice_service.py          # Vosk STT + Kokoro TTS
│       └── websocket_server.py       # Real-time broadcast to dashboard
├── tests/
│   ├── conftest.py                   # Shared fixtures
│   ├── test_analysis.py
│   ├── test_coordinator.py
│   ├── test_cortex_api.py
│   ├── test_cortex_memory.py
│   ├── test_cross_device_rules.py
│   ├── test_data_reader.py
│   ├── test_decision_engine.py
│   ├── test_e2e_flow.py              # E2E (requires running services)
│   ├── test_forecaster.py
│   ├── test_observations.py
│   ├── test_outcome_tracker.py
│   └── test_rule_advisor.py
├── Dockerfile
├── pytest.ini
├── requirements.txt
└── .env.example
```

## Dependencies

Requires Python 3.12+. External services:

- **MQTT broker** (Mosquitto) — telemetry transport
- **Redis** — hot data storage (optional; SQLite-only mode if unavailable)
- **Ollama** — local LLM for escalation + chat (optional; rules-only mode if unavailable)
- **ffmpeg** — audio format conversion (required for voice features)