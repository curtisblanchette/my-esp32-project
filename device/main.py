from lib.switch import SWITCH
from lib.pulse import PULSE
from lib.sensors.temp import TempSensor
from lib.home_hub import HomeHubClient
from lib import wifi
from secrets import SSID, PASSWORD

import json
import time
from services.mqtt import MqttService

try:
    from secrets import MQTT_HOST, MQTT_PORT, MQTT_CLIENT_ID, DEVICE_LOCATION, DEVICE_NAME
    from secrets import SENSORS, ACTUATORS, TELEMETRY_INTERVAL_MS
except ImportError:
    MQTT_HOST = "192.168.1.84"
    MQTT_PORT = 1883
    MQTT_CLIENT_ID = "esp32-1"
    DEVICE_LOCATION = "Grow Room"
    DEVICE_NAME = "Grow System"
    SENSORS = "[]"
    ACTUATORS = "[]"
    TELEMETRY_INTERVAL_MS = 5000

# Firmware version
FIRMWARE_VERSION = "1.2.0"

# Parse capabilities from registry config
sensors = json.loads(SENSORS)
actuators = json.loads(ACTUATORS)

# Ensure Wi-Fi is connected before starting services
wifi.ensure_connected(SSID, PASSWORD)

# Setup MQTT
mqtt = MqttService(
    client_id=MQTT_CLIENT_ID,
    host=MQTT_HOST,
    port=int(MQTT_PORT),
    keepalive=30,
)

# Setup HomeHub client
hub = HomeHubClient(
    device_id=MQTT_CLIENT_ID,
    name=DEVICE_NAME,
    location=DEVICE_LOCATION,
    mqtt_client=mqtt
)
hub.set_firmware_version(FIRMWARE_VERSION)

# --- Initialize actuators ---
actuator_pins = {}  # id -> SWITCH|PULSE
for a in actuators:
    if a["type"] == "switch":
        pin = SWITCH(a["pin"])
    elif a["type"] == "momentary":
        pin = PULSE(a["pin"])
    else:
        pin = SWITCH(a["pin"])

    actuator_pins[a["id"]] = pin
    hub.register_actuator(a["id"], a["type"], name=a["name"], state=bool(pin.value()))


# --- Initialize sensors ---
# Group by driver+pin to avoid duplicate hardware instances
# (e.g., DHT11 on pin 2 provides both temp and humidity)
sensor_drivers = {}  # (driver, pin) -> TempSensor instance
SENSOR_PRESENT = False

for s in sensors:
    hub.register_sensor(s["id"], s["type"], unit=s.get("unit"))
    driver = s.get("driver")
    pin = s.get("pin")
    if driver and pin:
        key = (driver, pin)
        if key not in sensor_drivers:
            try:
                hw = TempSensor(pin=pin, sensor=driver)
                hw.read()  # probe to verify sensor is connected
                sensor_drivers[key] = hw
                SENSOR_PRESENT = True
                print(f"[Sensor] {driver} detected on pin {pin}")
            except:
                print(f"[Sensor] No {driver} detected on pin {pin} - skipped")

# Build a reading plan: list of (sensor_id, driver_key, reading_index)
# DHT sensors return (temp, humidity) — map sensor type to tuple index
READING_INDEX = {"temperature": 0, "humidity": 1}
reading_plan = []
for s in sensors:
    driver = s.get("driver")
    pin = s.get("pin")
    key = (driver, pin)
    if key in sensor_drivers and s["type"] in READING_INDEX:
        reading_plan.append((s["id"], key, READING_INDEX[s["type"]]))

if not sensors:
    print("[Config] No sensors configured")
if not actuators:
    print("[Config] No actuators configured")

# Pending pulses: list of (target, correlation_id) waiting for tick() completion
pending_pulses = []

# Command handler
def handle_command(correlation_id, target, action, value, ttl):
    print(f"[Command] target={target} action={action} value={value} ttl={ttl}")

    if target in actuator_pins:
        pin = actuator_pins[target]
        if action == "pulse":
            if hasattr(pin, "is_busy") and pin.is_busy:
                hub.publish_ack(correlation_id, "rejected", target, None, error="pulse in progress (debounce)")
            else:
                pin.pulse()
                pending_pulses.append((target, correlation_id))
                hub.publish_ack(correlation_id, "executed", target, True)
        elif action == "set":
            if value:
                pin.on()
            else:
                pin.off()
            hub.publish_ack(correlation_id, "executed", target, value)
        elif action == "toggle":
            hub.publish_ack(correlation_id, "rejected", target, None, error="toggle not implemented")
        else:
            hub.publish_ack(correlation_id, "rejected", target, None, error=f"unknown action: {action}")
    else:
        hub.publish_ack(correlation_id, "rejected", target, None, error=f"unknown target: {target}")

# Register command handler (only if device has actuators)
if actuators:
    hub.on_command(handle_command)

# Configure last will (must be before connect)
hub.set_last_will()

# Connect and announce
mqtt.connect()
hub.publish_birth(telemetry_interval_ms=TELEMETRY_INTERVAL_MS)
print(f"[HomeHub] Device {MQTT_CLIENT_ID} online at {DEVICE_LOCATION}")

# Track uptime
boot_time = time.ticks_ms()

# Main loop
backoff = 1
next_telemetry = time.ticks_ms()

while True:
    try:
        if not mqtt.is_connected():
            mqtt.connect()
            hub.publish_birth(telemetry_interval_ms=TELEMETRY_INTERVAL_MS)
            backoff = 1
            next_telemetry = time.ticks_ms()

        # Check for incoming commands
        if actuators:
            hub.check_messages()

        # Read and publish telemetry on schedule
        now = time.ticks_ms()
        if time.ticks_diff(now, next_telemetry) >= 0:
            if SENSOR_PRESENT and reading_plan:
                # Read each unique driver once, cache results
                driver_readings = {}
                for key, hw in sensor_drivers.items():
                    try:
                        driver_readings[key] = hw.read()
                    except Exception as e:
                        print(f"[Sensor] Read error {key}: {e}")

                # Map readings to sensor IDs
                telemetry = []
                for sensor_id, key, idx in reading_plan:
                    if key in driver_readings:
                        telemetry.append({"id": sensor_id, "value": driver_readings[key][idx]})

                if telemetry:
                    hub.publish_telemetry(telemetry)
            else:
                # Keep MQTT connection alive when not publishing telemetry
                mqtt.ping()

            next_telemetry = time.ticks_add(now, TELEMETRY_INTERVAL_MS)

        # Check pending pulses for completion
        completed = []
        for i, (target, cid) in enumerate(pending_pulses):
            pin = actuator_pins[target]
            if pin.tick():
                hub.publish_ack(cid, "executed", target, False)
                completed.append(i)
            elif not pin.is_busy:
                completed.append(i)
        for i in reversed(completed):
            pending_pulses.pop(i)

        time.sleep(0.1)

    except Exception as e:
        print(f"[Error] {e}")
        try:
            mqtt.disconnect()
        except:
            pass

        time.sleep(backoff)
        backoff = min(backoff * 2, 30)
