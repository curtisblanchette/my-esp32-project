import urequests
import secrets

def update_sensor_value(sensor_value):
    # import secrets then getattr:
    #   api_base_url = getattr(secrets, "API_BASE_URL", "")
    # This typically silences IDE import warnings because it doesn't require that API_BASE_URL
    # is present in the stdlib stub at analysis time.
    api_base_url = getattr(secrets, "API_BASE_URL", "")
    url = f"{api_base_url}/?sensorValue={sensor_value}"
    resp = None
    try:
        resp = urequests.get(url)
        print("status:", resp.status_code)
        print("body:", resp.text)
    finally:
        if resp is not None:
            resp.close()

def ping_home():
    api_base_url = getattr(secrets, "API_BASE_URL", "")
    url = f"{api_base_url}/health?id=1"
    resp = None
    try:
        resp = urequests.get(url)
        print("status:", resp.status_code)
        print("body:", resp.text)
    finally:
        if resp is not None:
            resp.close()