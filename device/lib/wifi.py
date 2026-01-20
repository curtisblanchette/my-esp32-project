import network
import time

def scan():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    results = []
    for item in wlan.scan():
        ssid = item[0]
        if isinstance(ssid, bytes):
            ssid = ssid.decode("utf-8", "ignore")
        results.append(ssid)

    return results

def connect(ssid, password):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)

    while not wlan.isconnected():
        time.sleep(0.5)

    print("WiFi connected:", wlan.ifconfig())
    return wlan

def is_connected():
    wlan = network.WLAN(network.STA_IF)
    return wlan.active() and wlan.isconnected()

