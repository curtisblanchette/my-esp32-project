# ESP32 Device

MicroPython firmware for ESP32 sensor nodes. Communicates with Cortex backend via MQTT.

## Flash & Debug

All commands run from the **repo root**:

```bash
./tools/flash.sh <device-id>          # Upload MicroPython code via mpremote
./tools/flash.sh <device-id> --erase  # Full flash with MicroPython firmware
./tools/flash.sh --list               # List registered devices
./tools/repl.sh                       # Serial console monitor
./tools/reset.sh                      # Soft reset device
```

## MQTT

Uses the topic format defined in the root CLAUDE.md. Device publishes to `telemetry` and `birth` topics, subscribes to `command` topic, and publishes `ack` responses.

## Key Files

- `main.py` — Sensor loop + command handling
- `boot.py` — WiFi connection on startup
- `secrets.py` — Auto-generated credentials (**gitignored**, use template)
- `lib/home_hub.py` — `HomeHubClient` for standardized MQTT messaging
- `registry.json` — Device registry (**gitignored**)
