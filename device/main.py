from lib.led import LED

import time

from lib import wifi
from services import web
from secrets import SSID, PASSWORD

# Scan for Wi-Fi networks
print("Nearby Wi-Fi networks:")
ssids = wifi.scan()
for s in ssids:
    print(" -", s)

# Connect to Wi-Fi
if SSID in ssids:
    print(f"Found provided {SSID}")
    wifi.connect(SSID, PASSWORD)
    print(f"Connected to {SSID}")
    web.call_endpoint()
else:
    print(f"Could not find {SSID}")

# Setup Blink LED
led = LED(5)

# Main loop
while True:
    led.on()
    print("LED ON")
    time.sleep(1)
    led.off()
    print("LED OFF")
    time.sleep(1)