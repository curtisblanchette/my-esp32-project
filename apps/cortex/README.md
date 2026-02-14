# Cortex

The autonomous decision engine for Mycelium. Cortex processes real-time sensor telemetry through a rules engine with LLM escalation, and provides voice processing (STT/TTS) for the dashboard.

## Architecture

```
MQTT Telemetry ──→ Decision Engine ──→ MQTT Commands ──→ ESP32
                        │
                        ├── Rule evaluation (fast, synchronous)
                        │     duration guards, cooldown timers
                        │
                        └── LLM escalation (async, non-blocking)
                              rapid changes, conflicting rules,
                              unknown patterns → Ollama
```

**Voice pipeline** (proxied through the Node.js API):

```
Browser Audio → POST /voice/transcribe → Vosk STT → Text
Text Response → POST /voice/synthesize → Kokoro TTS → WAV Audio
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment config
cp .env.example .env

# Download voice models
mkdir -p models
wget -q https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip \
  && unzip -q vosk-model-small-en-us-0.15.zip -d models \
  && rm vosk-model-small-en-us-0.15.zip

curl -L -o models/kokoro-v1.0.onnx \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx

curl -L -o models/voices-v1.0.bin \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

# Run the orchestrator (MQTT loop + HTTP API)
python -m src.main
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

1. **Rule evaluation** -- checks sensor readings against thresholds with duration guards to prevent false positives and cooldown timers to prevent rapid toggling
2. **Command execution** -- publishes MQTT commands to devices when rules trigger
3. **LLM escalation** -- routes anomalies (rapid changes, conflicting rules, unknown patterns) to Ollama for deeper analysis when rules alone aren't sufficient

### Voice Processing

The HTTP API exposes STT and TTS endpoints consumed by the Node.js API:

- **Vosk** -- offline speech-to-text (~40MB model, 16kHz mono)
- **Kokoro** -- ONNX-based text-to-speech with 20 voice options (24kHz WAV output)

Text normalization handles numbers, times, currency, percentages, ordinals, emojis, and markdown before synthesis. Long text is chunked to stay within Kokoro's 510 phoneme limit.

## Rules Configuration

Rules live in `config/rules.yaml`:

```yaml
rules:
  - name: high_temp_alert
    description: "Turn on fan when temperature exceeds threshold"
    condition:
      sensor: temp1
      operator: ">"        # supports: > < >= <= == !=
      threshold: 25
      duration_seconds: 15 # condition must hold this long
    action:
      target: relay1
      action: set
      value: true
      reason: "Temperature exceeded threshold"

llm:
  enabled: true
  model: "phi3:mini"
  escalation_triggers:
    - rapid_change: 5          # degrees per minute
    - conflicting_rules: true
    - unknown_pattern: true
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health (STT, TTS, LLM availability) |
| `POST` | `/voice/transcribe` | Audio file upload &rarr; text transcription |
| `POST` | `/voice/synthesize` | `{"message": "..."}` &rarr; WAV audio |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_HOST` | `localhost` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | -- | Broker auth username |
| `MQTT_PASSWORD` | -- | Broker auth password |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.2:3b` | LLM model for escalation |
| `API_URL` | `http://localhost:3000` | Node.js API (for historical context) |
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
│   └── rules.yaml              # Automation rules
├── models/                     # ML models (gitignored)
│   ├── vosk-model-small-en-us-0.15/
│   ├── kokoro-v1.0.onnx
│   └── voices-v1.0.bin
├── src/
│   ├── main.py                 # Orchestrator entry point
│   ├── api.py                  # FastAPI HTTP server
│   ├── config.py               # Environment config loader
│   ├── models/
│   │   ├── telemetry.py        # TelemetryMessage & Reading
│   │   └── command.py          # Command & CommandAck
│   └── services/
│       ├── shared.py           # SharedServices singleton
│       ├── decision_engine.py  # Rules engine + LLM escalation
│       ├── mqtt_client.py      # MQTT connection & handlers
│       ├── ollama_client.py    # LLM client (async thread pool)
│       └── voice_service.py    # Vosk STT + Kokoro TTS
├── Dockerfile
├── requirements.txt
└── .env.example
```

## MQTT Topics

Cortex subscribes to:

- `home/+/+/telemetry` -- sensor readings
- `home/+/+/ack` -- command acknowledgments
- `home/_registry/+/birth` -- device registration
- `home/_registry/+/will` -- device offline (LWT)

Cortex publishes to:

- `home/{location}/{deviceId}/command` -- actuator commands

## Dependencies

Requires Python 3.12+. External services:

- **MQTT broker** (Mosquitto) -- telemetry transport
- **Ollama** -- local LLM for escalation (optional; rules-only mode if unavailable)
- **ffmpeg** -- audio format conversion (required for voice features)
