# Device Lifecycle: Birth, Telemetry, and Last Will

How ESP32 devices register, communicate, and are detected as offline.

## Overview

```
Device Power On
    │
    ├─ WiFi connect
    ├─ Set LWT on MQTT client (before connect)
    ├─ MQTT connect ──► Broker stores LWT
    ├─ Publish birth ──► Server registers device, broadcasts to dashboard
    │
    ▼
Main Loop (100ms poll)
    ├─ Check for incoming commands (hub.check_messages)
    ├─ Publish telemetry on schedule (every TELEMETRY_INTERVAL_MS)
    └─ time.sleep(0.1)
    │
    ▼
Disconnection
    ├─ Clean disconnect ──► Broker discards LWT (no will published)
    └─ Unclean disconnect ──► Broker publishes LWT after keepalive timeout
```

## Birth Message

On connect, the device publishes a registration message announcing its identity and capabilities.

**Topic:** `home/_registry/{deviceId}/birth`

**Payload:**
```json
{
  "v": 1,
  "ts": 1700000000000,
  "deviceId": "esp32-1",
  "location": "Grow Room",
  "type": "birth",
  "payload": {
    "name": "Grow System",
    "platform": "esp32",
    "firmware": "1.2.0",
    "capabilities": {
      "sensors": [
        { "id": "temp1", "type": "temperature", "unit": "celsius" },
        { "id": "hum1", "type": "humidity", "unit": "percent" }
      ],
      "actuators": [
        { "id": "relay1", "type": "switch", "name": "Grow Light", "state": false }
      ]
    },
    "telemetryIntervalMs": 5000
  }
}
```

**Server handling** (`mqttTelemetry.ts → handleBirth`):
1. Upserts device record in SQLite (capabilities, location, firmware, online=true)
2. Inserts a `device_birth` event
3. Broadcasts updated device list via WebSocket

Birth is also re-published on every reconnect (e.g., after WiFi drop + recovery).

## Last Will and Testament (LWT)

MQTT LWT is the mechanism for detecting unclean device disconnections (crash, power loss, WiFi failure). The will message is **not sent by the device** — it is stored by the **broker** at connect time and published on the device's behalf when the broker determines the client is gone.

### Configuration

The device registers its LWT **before** calling `mqtt.connect()`:

```
device/main.py:119-123

hub.set_last_will()    # Registers will with MQTT client
mqtt.connect()         # Broker stores will during CONNECT handshake
```

**Topic:** `home/_registry/{deviceId}/will`

**Payload:**
```json
{
  "v": 1,
  "deviceId": "esp32-1",
  "type": "will",
  "payload": { "status": "offline" }
}
```

### When the Broker Publishes the Will

| Scenario | Will Published? | Timing |
|----------|----------------|--------|
| Device loses power / crashes | Yes | ~45s (1.5x keepalive) |
| WiFi drops (no TCP RST) | Yes | ~45s (1.5x keepalive) |
| WiFi drops (TCP RST sent) | Yes | Immediate |
| Network cable unplugged | Yes | ~45s (1.5x keepalive) |
| `mqtt.disconnect()` called | **No** | Will is discarded |
| Broker restarts | **No** | Will is discarded |

### Keepalive Timing

- **Device keepalive**: 30 seconds (`device/main.py:39`, passed to `umqtt.simple`)
- **Broker will timeout**: **~45 seconds** (MQTT 3.1.1 spec requires the broker to wait 1.5x the keepalive before considering the client dead)
- **Mosquitto config**: No custom keepalive override — uses the client-negotiated value

The keepalive mechanism works by the broker tracking when the last PINGREQ or any other packet was received from the client. If no packet arrives within 1.5x the keepalive interval, the broker closes the connection and publishes the will.

The device keeps the connection alive during its main loop via:
- `hub.publish_telemetry()` — every `TELEMETRY_INTERVAL_MS` (5000ms default), resets the keepalive timer
- `mqtt.ping()` — sent when no telemetry to publish (no sensors), resets the keepalive timer

### Server Handling

The API server subscribes to `home/_registry/+/will` and processes will messages in `mqttTelemetry.ts → handleWill`:

1. Marks device offline in SQLite (`setDeviceOffline`)
2. Inserts a `device_offline` event
3. Broadcasts updated device list via WebSocket → dashboard shows device as offline

### Key Files

| File | Role |
|------|------|
| `device/lib/home_hub.py:87-100` | `set_last_will()` — builds will envelope, passes to MQTT client |
| `device/services/mqtt.py:93-108` | `set_last_will()` — stores will config, applied before `connect()` |
| `device/services/mqtt.py:29-51` | `connect()` — calls `MQTTClient.set_last_will()` then `connect()` |
| `device/main.py:119-124` | Initialization — sets will, connects, publishes birth |
| `apps/api/src/services/mqttTelemetry.ts:81-82` | Subscribes to `home/_registry/+/will` |
| `apps/api/src/services/mqttTelemetry.ts:348-365` | `handleWill()` — marks device offline, broadcasts |
| `mosquitto/mosquitto.conf` | Broker config (no custom keepalive settings) |

## Clean vs Unclean Disconnect

**Clean disconnect** (`mqtt.disconnect()`): The device sends a DISCONNECT packet. The broker removes the session and **discards** the stored will. No will message is published. This is the expected path for intentional shutdowns or firmware updates.

**Unclean disconnect** (crash, power loss, WiFi drop): The TCP connection goes stale. The broker waits 1.5x keepalive (45s), then publishes the stored will message to all subscribers of the will topic. This is the offline detection path.

Note: The device's error handler (`device/main.py:174-182`) calls `mqtt.disconnect()` on exceptions before reconnecting. This means transient errors that the device recovers from will trigger a clean disconnect (no will), followed by a fresh birth on reconnect. The dashboard sees the device go offline briefly then come back online.