# Local AI Orchestrator for ESP32 IoT System

## Overview

Add a local AI service that monitors sensor readings and orchestrates device control via MQTT commands.

## Implementation Status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | MicroPython Device Library | ✅ Complete |
| 1 | Command Infrastructure (Backend) | ✅ Complete |
| 2 | ESP32 Device Updates | ✅ Complete |
| 3 | AI Orchestrator Service | ✅ Complete |
| 4 | Docker Integration | ✅ Complete |
| 5 | Dashboard Updates | ✅ Complete |

**Completed:** 2026-01-30

## User Preferences
- **AI Approach:** Hybrid (rules + LLM)
- **Hardware:** Simulation only (log commands, no physical relays yet)
- **LLM Runtime:** Ollama

## Architecture

```
ESP32 Device <--> MQTT Broker <--> AI Orchestrator (Python)
     |                 |                    |
     |                 v                    |
     |          Node.js API <---------------+
     |                 |         (HTTP API)
     |                 v
     |          WebSocket --> React Dashboard
```

## Key Design Decisions

1. **Separate Python service** - Best LLM ecosystem, decoupled from Node.js
2. **Ollama** - Local LLM server with easy model management
3. **Hybrid rule + LLM** - Fast rules for thresholds, LLM for complex patterns
4. **Direct MQTT** - AI subscribes to telemetry, publishes commands directly

## Implementation Phases

### Phase 0: MicroPython Device Library ✅
**Status:** Complete

**Files created/modified:**
- `device/lib/home_hub.py` - HomeHubClient class (created)
- `device/services/mqtt.py` - Added subscribe, set_callback, check_msg, set_last_will methods
- `device/main.py` - Refactored to use HomeHubClient with command handling

**New file:** `device/lib/home_hub.py`

A shared library all ESP32 devices use to implement the messaging contract:

```python
import json
import time

class HomeHubClient:
    VERSION = 1

    def __init__(self, device_id: str, location: str, mqtt_client, platform: str = "esp32"):
        self.device_id = device_id
        self.location = location
        self.platform = platform
        self.mqtt = mqtt_client
        self.capabilities = {"sensors": [], "actuators": []}
        self._command_handler = None
        self._firmware_version = "1.0.0"

    def set_firmware_version(self, version: str):
        self._firmware_version = version

    def register_sensor(self, id: str, type: str, unit: str = None, values: list = None):
        """Register a sensor capability (temp, humidity, contact, motion, etc.)"""
        sensor = {"id": id, "type": type}
        if unit: sensor["unit"] = unit
        if values: sensor["values"] = values
        self.capabilities["sensors"].append(sensor)

    def register_actuator(self, id: str, type: str, name: str, **kwargs):
        """Register an actuator capability (switch, dimmer, cover, etc.)"""
        actuator = {"id": id, "type": type, "name": name, **kwargs}
        self.capabilities["actuators"].append(actuator)

    def publish_birth(self, telemetry_interval_ms: int = 5000):
        """Announce device presence and capabilities"""
        payload = {
            "name": f"{self.location.title()} {self.device_id}",
            "platform": self.platform,
            "firmware": self._firmware_version,
            "capabilities": self.capabilities,
            "telemetryIntervalMs": telemetry_interval_ms
        }
        self._publish("birth", payload, topic=f"home/_registry/{self.device_id}/birth")

    def set_last_will(self):
        """Configure MQTT last-will message (call before connect)"""
        will_topic = f"home/_registry/{self.device_id}/will"
        will_msg = json.dumps({"v": self.VERSION, "deviceId": self.device_id, "type": "will", "payload": {"status": "offline"}})
        self.mqtt.set_last_will(will_topic, will_msg)

    def publish_telemetry(self, readings: list):
        """Publish sensor readings: [{"id": "temp1", "value": 23.5}, ...]"""
        self._publish("telemetry", {"readings": readings})

    def publish_status(self, uptime_ms: int, free_heap: int = None, rssi: int = None):
        """Publish device health/heartbeat"""
        payload = {"uptime": uptime_ms}
        if free_heap: payload["freeHeap"] = free_heap
        if rssi: payload["rssi"] = rssi
        self._publish("status", payload)

    def publish_ack(self, correlation_id: str, status: str, target: str, actual_value, error: str = None):
        """Acknowledge a command: status = executed|rejected|error|expired"""
        payload = {"correlationId": correlation_id, "status": status, "target": target, "actualValue": actual_value}
        if error: payload["error"] = error
        self._publish("ack", payload)

    def on_command(self, handler):
        """Set command handler and subscribe to command topic"""
        self._command_handler = handler
        topic = f"home/{self.location}/{self.device_id}/command"
        self.mqtt.subscribe(topic)
        self.mqtt.set_callback(self._handle_message)

    def _handle_message(self, topic: bytes, msg: bytes):
        """Parse incoming command and invoke handler"""
        try:
            data = json.loads(msg.decode())
            if data.get("type") == "command" and self._command_handler:
                payload = data.get("payload", {})
                self._command_handler(
                    correlation_id=data.get("correlationId"),
                    target=payload.get("target"),
                    action=payload.get("action"),
                    value=payload.get("value"),
                    ttl=payload.get("ttl")
                )
        except Exception as e:
            print(f"Command parse error: {e}")

    def check_messages(self):
        """Non-blocking check for incoming commands (call in main loop)"""
        self.mqtt.check_msg()

    def _publish(self, msg_type: str, payload: dict, topic: str = None):
        """Internal: wrap payload in envelope and publish"""
        envelope = {
            "v": self.VERSION,
            "ts": int(time.time() * 1000),
            "deviceId": self.device_id,
            "location": self.location,
            "type": msg_type,
            "payload": payload
        }
        topic = topic or f"home/{self.location}/{self.device_id}/{msg_type}"
        self.mqtt.publish(topic.encode(), json.dumps(envelope).encode())
```

**Example device usage (`device/main.py`):**
```python
from lib.home_hub import HomeHubClient
from lib.sensors.temp import TempSensor
from services.mqtt import MqttService

# Setup
mqtt = MqttService(host=MQTT_HOST, port=MQTT_PORT, client_id=DEVICE_ID)
hub = HomeHubClient(DEVICE_ID, LOCATION, mqtt)

# Register capabilities
hub.register_sensor("temp1", "temperature", unit="celsius")
hub.register_sensor("hum1", "humidity", unit="percent")
hub.register_actuator("relay1", "switch", name="Light")

# Command handler
def handle_command(correlation_id, target, action, value, ttl):
    print(f"Command: {target} {action}={value}")
    # TODO: Execute GPIO
    hub.publish_ack(correlation_id, "executed", target, value)

hub.on_command(handle_command)
hub.set_last_will()
mqtt.connect()
hub.publish_birth()

# Main loop
while True:
    hub.check_messages()
    temp, hum = sensor.read()
    hub.publish_telemetry([
        {"id": "temp1", "value": temp},
        {"id": "hum1", "value": hum}
    ])
    time.sleep(5)
```

### Phase 1: Command Infrastructure (Backend) ✅
**Status:** Complete

**Files modified:**
- `apps/api/src/services/mqttTelemetry.ts` - Added publishCommand(), getMqttClient(), subscription to new topics, ack handling
- `apps/api/src/routes/api.ts` - Added POST/GET /api/commands, GET /api/events, wired relay control to MQTT
- `apps/api/src/lib/sqlite.ts` - Added events + commands tables with CRUD functions

**Changes:**
- Export MQTT client for publishing
- Subscribe to `/device/+/ack` topics
- Create `POST /api/commands` endpoint
- Add new tables to SQLite (leveraging existing `sensor_readings` and `relay_config`):

```sql
-- Discrete events (door open, motion, commands, acks)
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  device_id TEXT NOT NULL,
  event_type TEXT NOT NULL,  -- 'door_open', 'motion', 'command_sent', 'command_ack'
  payload JSON,
  source TEXT  -- 'device', 'ai-orchestrator', 'dashboard'
);
CREATE INDEX idx_events_ts ON events(ts);
CREATE INDEX idx_events_device ON events(device_id);

-- Command audit trail
CREATE TABLE commands (
  id TEXT PRIMARY KEY,  -- correlationId
  ts INTEGER NOT NULL,
  device_id TEXT NOT NULL,
  target TEXT NOT NULL,
  action TEXT NOT NULL,
  value JSON,
  source TEXT NOT NULL,
  reason TEXT,
  status TEXT DEFAULT 'pending',  -- 'pending', 'acked', 'failed', 'expired'
  ack_ts INTEGER,
  ack_payload JSON
);
```

**Existing tables (no changes needed):**
- `sensor_readings` - Continuous telemetry (temp, humidity) - used for trend analysis
- `relay_config` - Relay definitions

### Phase 2: ESP32 Device Updates (Simulation Mode) ✅
**Status:** Complete (implemented during Phase 0)

**Implemented in Phase 0:**
- `device/services/mqtt.py` - subscribe(), set_callback(), check_msg(), set_last_will()
- `device/main.py` - Command handler controls LED as relay simulation, publishes acks

**Features:**
- Subscribes to `home/{location}/{device_id}/command`
- Command handler with LED control (simulation)
- Publishes acks to `home/{location}/{device_id}/ack`
- Birth message on connect, last-will for offline detection

### Phase 3: AI Orchestrator Service ✅
**Status:** Complete

**New directory:** `apps/ai/`

```
apps/ai/
├── Dockerfile
├── requirements.txt
├── .env.example
├── config/
│   └── rules.yaml
└── src/
    ├── __init__.py
    ├── main.py              # Orchestrator class, entry point
    ├── config.py            # Environment configuration
    ├── services/
    │   ├── __init__.py
    │   ├── mqtt_client.py   # MQTT subscribe/publish with callbacks
    │   ├── ollama_client.py # LLM integration with JSON responses
    │   └── decision_engine.py # Rule evaluation with duration/cooldown
    └── models/
        ├── __init__.py
        ├── telemetry.py     # TelemetryMessage, Reading dataclasses
        └── command.py       # Command, CommandAck dataclasses
```

**Core components:**
- MQTT client subscribing to `home/+/+/telemetry`, `home/+/+/ack`, `home/_registry/+/birth|will`
- Rule-based decision engine (YAML config) with duration tracking and cooldowns
- Ollama integration for complex reasoning with JSON response parsing
- Command publisher with correlation IDs
- LLM escalation triggers (rapid temp change, unknown patterns)

**Context/Memory Architecture:**

The AI builds context from existing storage before each LLM call:

```python
class ContextBuilder:
    def build_prompt_context(self, current_reading) -> str:
        # 1. Recent readings from Redis (last 2 hours, hot data)
        recent_readings = redis.get_readings(since=now - 2h)

        # 2. Discrete events from SQLite (last 24 hours)
        recent_events = sqlite.query("""
            SELECT * FROM events
            WHERE ts > ? ORDER BY ts DESC LIMIT 50
        """, now - 24h)

        # 3. Trend summary from sensor_readings (last 7 days)
        trends = sqlite.query("""
            SELECT
                strftime('%H', ts/1000, 'unixepoch') as hour,
                AVG(temp) as avg_temp,
                AVG(humidity) as avg_humidity
            FROM sensor_readings
            WHERE ts > ?
            GROUP BY hour
        """, now - 7d)

        # 4. Recent commands and their outcomes
        recent_commands = sqlite.query("""
            SELECT * FROM commands
            WHERE ts > ? ORDER BY ts DESC LIMIT 20
        """, now - 24h)

        return format_context(
            current=current_reading,
            recent_readings=recent_readings,
            events=recent_events,
            trends=trends,
            commands=recent_commands
        )
```

**Example LLM prompt with context:**
```
Current: temp=29°C, humidity=65%, ts=2026-01-29 17:47:00

Recent Events (last 2 hours):
- 17:45 garage door_opened (device: garage-sensor)
- 17:46 motion_detected (device: hallway-pir)
- 17:35 command_sent relay2=OFF (source: ai-orchestrator)
- 17:30 temp reading 27.5°C

Typical Patterns:
- Weekday 5-6pm: garage opens, temp rises ~1°C over 15min
- Current temp is 2°C above hourly average for this time

Question: Temperature rising. What action should be taken?
```

### Phase 4: Docker Integration ✅
**Status:** Complete

**File:** `docker-compose.yml`

**Added services:**
- `ollama` - Local LLM server (ollama/ollama:latest) on port 11434
- `ai` - Python orchestrator service built from apps/ai/Dockerfile

**Service dependencies:**
```
mosquitto ─┬─► api ─► web
           │
           ├─► ai
           │
ollama ────┘
```

**To start:** `docker-compose up -d && docker exec my-esp32-ollama ollama pull phi3:mini`

### Phase 5: Dashboard Updates ✅
**Status:** Complete

**Files created:**
- `apps/web/src/components/AIStatusIndicator.tsx` - Shows AI active/inactive status
- `apps/web/src/components/RecentActivity.tsx` - Shows recent commands/events with source badges

**Files modified:**
- `apps/web/src/api.ts` - Added fetchCommands(), fetchEvents(), Command/DeviceEvent types
- `apps/web/src/App.tsx` - Integrated AI status indicator and activity panel

**Features:**
- AI status indicator in header (purple when active, shows last command time)
- Recent Activity panel with source badges: AI (purple), Manual (blue), Device (green)
- Command status icons (checkmark/X/spinner)
- Auto-refresh every 10 seconds

## MQTT Messaging Contract

### Topic Structure
```
home/{location}/{device_id}/{message_type}

Examples:
home/garage/esp32-001/telemetry
home/garage/esp32-001/command
home/garage/esp32-001/ack
home/garage/esp32-001/status

System topics:
home/_registry/+/birth    # Device registration
home/_registry/+/will     # Device offline (last-will)
```

### Message Envelope (All Messages)
```json
{
  "v": 1,
  "ts": 1706540000000,
  "deviceId": "esp32-001",
  "location": "garage",
  "type": "telemetry|command|ack|status|birth|will",
  "payload": { }
}
```

### Device Registration (Birth)
**Topic:** `home/_registry/{device_id}/birth`
```json
{
  "v": 1,
  "ts": 1706540000000,
  "deviceId": "esp32-001",
  "location": "garage",
  "type": "birth",
  "payload": {
    "name": "Garage Sensor Hub",
    "platform": "esp32",
    "firmware": "1.2.0",
    "capabilities": {
      "sensors": [
        { "id": "temp1", "type": "temperature", "unit": "celsius" },
        { "id": "hum1", "type": "humidity", "unit": "percent" },
        { "id": "door1", "type": "contact", "values": ["open", "closed"] }
      ],
      "actuators": [
        { "id": "relay1", "type": "switch", "name": "Garage Light" },
        { "id": "relay2", "type": "switch", "name": "Fan" }
      ]
    },
    "telemetryIntervalMs": 5000
  }
}
```

### Telemetry (Sensor Readings)
**Topic:** `home/{location}/{device_id}/telemetry`
```json
{
  "v": 1,
  "ts": 1706540000000,
  "deviceId": "esp32-001",
  "location": "garage",
  "type": "telemetry",
  "payload": {
    "readings": [
      { "id": "temp1", "value": 23.5, "unit": "celsius" },
      { "id": "hum1", "value": 65, "unit": "percent" },
      { "id": "door1", "value": "closed" }
    ]
  }
}
```

### Commands (To Devices)
**Topic:** `home/{location}/{device_id}/command`
```json
{
  "v": 1,
  "ts": 1706540000000,
  "correlationId": "cmd-abc123",
  "source": "ai-orchestrator",
  "deviceId": "esp32-001",
  "location": "garage",
  "type": "command",
  "payload": {
    "target": "relay1",
    "action": "set",
    "value": true,
    "reason": "Temperature exceeded 28°C",
    "ttl": 30000
  }
}
```
**Actions:** `set`, `toggle`, `pulse`, `query`

### Acknowledgments (From Devices)
**Topic:** `home/{location}/{device_id}/ack`
```json
{
  "v": 1,
  "ts": 1706540001000,
  "deviceId": "esp32-001",
  "location": "garage",
  "type": "ack",
  "payload": {
    "correlationId": "cmd-abc123",
    "status": "executed",
    "target": "relay1",
    "actualValue": true
  }
}
```
**Status values:** `executed`, `rejected`, `error`, `expired`, `queued`

### Device Status (Heartbeat)
**Topic:** `home/{location}/{device_id}/status`
```json
{
  "v": 1,
  "ts": 1706540000000,
  "deviceId": "esp32-001",
  "location": "garage",
  "type": "status",
  "payload": {
    "uptime": 3600000,
    "freeHeap": 45000,
    "rssi": -65
  }
}
```

### Extensibility
**New sensor types:**
```json
{ "id": "lux1", "type": "illuminance", "unit": "lux" }
{ "id": "co2", "type": "co2", "unit": "ppm" }
{ "id": "motion1", "type": "motion", "values": ["detected", "clear"] }
```

**New actuator types:**
```json
{ "id": "dimmer1", "type": "dimmer", "min": 0, "max": 100 }
{ "id": "blind1", "type": "cover", "values": ["open", "close", "stop"] }
{ "id": "rgb1", "type": "rgb", "format": "hex" }
```

## Rule Configuration Example

```yaml
rules:
  - name: "high_temp_fan_on"
    condition:
      sensor: "temp"
      operator: ">"
      threshold: 28
      duration_seconds: 30
    action:
      target: "relay2"
      value: true
      reason: "Temperature exceeded 28C for 30s"
```

## Verification

1. Start Ollama and pull model: `ollama pull phi3:mini`
2. Start stack: `docker-compose up -d`
3. Simulate high temp reading via MQTT
4. Verify AI detects and publishes command
5. Verify ESP32 receives and executes
6. Verify dashboard shows updated state