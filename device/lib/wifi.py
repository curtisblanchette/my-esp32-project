import network
import time

# Reuse a single WLAN instance to avoid heap churn
_wlan = network.WLAN(network.STA_IF)


def scan():
    _wlan.active(True)

    results = []
    for item in _wlan.scan():
        ssid = item[0]
        if isinstance(ssid, bytes):
            ssid = ssid.decode("utf-8", "ignore")
        results.append(ssid)

    return results


def connect(ssid, password, timeout_s=10):
    """
    Attempt to connect to Wi-Fi, blocking up to timeout_s seconds.
    Returns True if connected, False otherwise.
    """
    _wlan.active(True)

    if _wlan.isconnected():
        return True

    _wlan.connect(ssid, password)

    start = time.ticks_ms()
    while not _wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), start) > timeout_s * 1000:
            return False
        time.sleep(0.25)

    print("WiFi connected:", _wlan.ifconfig())
    return True


def ensure_connected(ssid, password, max_backoff_s=30):
    """
    Ensure Wi-Fi is connected.
    Uses exponential backoff and resets the interface on repeated failure.
    """
    backoff = 1

    while True:
        if connect(ssid, password):
            return _wlan

        try:
            _wlan.disconnect()
        except:
            pass

        time.sleep(backoff)
        backoff = min(backoff * 2, max_backoff_s)


def is_connected():
    return _wlan.active() and _wlan.isconnected()


def ifconfig():
    if _wlan.isconnected():
        return _wlan.ifconfig()
    return None
