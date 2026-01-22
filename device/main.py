from lib.led import LED
from lib.sensors.temp import TempSensor
from lib import wifi
from secrets import SSID, PASSWORD

import time
import json
from services.mqtt import MqttService

try:
    from secrets import MQTT_HOST, MQTT_PORT, MQTT_TOPIC, MQTT_CLIENT_ID
except Exception:
    MQTT_HOST = "192.168.1.84"
    MQTT_PORT = 1883
    MQTT_TOPIC = "/device/esp32-1/telemetry"
    MQTT_CLIENT_ID = "esp32-1"

# Ensure Wi‑Fi is connected before starting services
wifi.ensure_connected(SSID, PASSWORD)

mqtt = MqttService(
    client_id=MQTT_CLIENT_ID,
    host=MQTT_HOST,
    port=int(MQTT_PORT),
    keepalive=30,
)

# Setup Blink LED
led = LED(5)

# Setup Temp Sensor
temp_sensor = TempSensor()

# Main loop
backoff = 1

while True:
    try:
        if not mqtt.is_connected():
            mqtt.connect()
            backoff = 1

        temp, humidity = temp_sensor.read()

        payload = {
            "tempC": temp,
            "humidity": humidity,
            "ts": int(time.time()),
        }

        mqtt.publish(MQTT_TOPIC, json.dumps(payload))

        led.on()
        time.sleep(1)
        led.off()
        time.sleep(1)

    except Exception:
        # Reset MQTT and back off on any failure
        try:
            mqtt.disconnect()
        except:
            pass

        time.sleep(backoff)
        backoff = min(backoff * 2, 30)