from lib.led import LED
from lib.sensors.temp import TempSensor
from lib.home_hub import HomeHubClient
from lib import wifi
from secrets import SSID, PASSWORD

import time
from services.mqtt import MqttService

try:
    from secrets import MQTT_HOST, MQTT_PORT, MQTT_CLIENT_ID, DEVICE_LOCATION
    from secrets import SENSOR_PIN, SENSOR_TYPE, LED_PIN
except ImportError:
    MQTT_HOST = "192.168.1.84"
    MQTT_PORT = 1883
    MQTT_CLIENT_ID = "esp32-1"
    DEVICE_LOCATION = "room1"
    SENSOR_PIN = 2
    SENSOR_TYPE = "DHT11"
    LED_PIN = 5

# Firmware version
FIRMWARE_VERSION = "1.1.0"
# TODO: make this a registry.json item. This way devices (once flashed) will broadcast their own interval timing.
#  helpful if devices require different interval telemetry
#
# TODO Plan:
#  the devices "· Offline" badge is aware of missed telemetry
#  showing offline only after missing 3 intervals
#  warning users the device will be removed from the dashboard
#  "in... <countdown_from_15>"
TELEMETRY_INTERVAL_MS = 5000

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
    location=DEVICE_LOCATION,
    mqtt_client=mqtt
)
hub.set_firmware_version(FIRMWARE_VERSION)

# Setup LED (before registering so we can report initial state)
led = LED(LED_PIN)

# Register capabilities
hub.register_sensor("temp1", "temperature", unit="celsius")
hub.register_sensor("hum1", "humidity", unit="percent")
hub.register_actuator("relay1", "switch", name="Status LED", state=bool(led.value()))

# Setup Temp Sensor
temp_sensor = TempSensor(pin=SENSOR_PIN, sensor=SENSOR_TYPE)

# Detect if sensor is connected
def detect_sensor():
    try:
        temp_sensor.read()
        return True
    except:
        return False

SENSOR_PRESENT = detect_sensor()
if SENSOR_PRESENT:
    print(f"[Sensor] {SENSOR_TYPE} detected on pin {SENSOR_PIN}")
else:
    print(f"[Sensor] No {SENSOR_TYPE} detected - telemetry disabled")

# Command handler (simulation mode - log only)
def handle_command(correlation_id, target, action, value, ttl):
    print(f"[Command] target={target} action={action} value={value} ttl={ttl}")

    if target == "relay1":
        # Simulate relay control via LED
        if action == "set":
            if value:
                led.on()
            else:
                led.off()
            hub.publish_ack(correlation_id, "executed", target, value)
        elif action == "toggle":
            # Toggle not implemented yet
            hub.publish_ack(correlation_id, "rejected", target, None, error="toggle not implemented")
        else:
            hub.publish_ack(correlation_id, "rejected", target, None, error=f"unknown action: {action}")
    else:
        hub.publish_ack(correlation_id, "rejected", target, None, error=f"unknown target: {target}")

# Register command handler
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

while True:
    try:
        if not mqtt.is_connected():
            mqtt.connect()
            hub.publish_birth(telemetry_interval_ms=TELEMETRY_INTERVAL_MS)
            backoff = 1

        # Check for incoming commands
        hub.check_messages()

        # Read and publish telemetry if sensor present
        if SENSOR_PRESENT:
            temp, humidity = temp_sensor.read()
            hub.publish_telemetry([
                {"id": "temp1", "value": temp},
                {"id": "hum1", "value": humidity}
            ])
        else:
            # Keep MQTT connection alive when not publishing telemetry
            mqtt.ping()

        # Avoid tight loop, stay responsive to commands
        time.sleep(TELEMETRY_INTERVAL_MS // 1000)

    except Exception as e:
        print(f"[Error] {e}")
        # Reset MQTT and back off on any failure
        try:
            mqtt.disconnect()
        except:
            pass

        time.sleep(backoff)
        backoff = min(backoff * 2, 30)
