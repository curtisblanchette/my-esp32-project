"""Sensor metadata: type → unit/label mapping + resolution helpers."""

# Maps sensor type string to default display metadata.
# Device-reported units take priority when available.
SENSOR_TYPE_DEFAULTS: dict[str, dict[str, str]] = {
    "temperature":   {"unit": "\u00b0C",  "label": "Temperature",   "rate_unit": "\u00b0C/min"},
    "humidity":      {"unit": "%",   "label": "Humidity",      "rate_unit": "%/min"},
    "soil_moisture": {"unit": "%",   "label": "Soil Moisture", "rate_unit": "%/min"},
    "contact":       {"unit": "",    "label": "Contact",       "rate_unit": ""},
    "motion":        {"unit": "",    "label": "Motion",        "rate_unit": ""},
    "light_level":   {"unit": "lux", "label": "Light Level",   "rate_unit": "lux/min"},
    "pressure":      {"unit": "hPa", "label": "Pressure",      "rate_unit": "hPa/min"},
    "co2":           {"unit": "ppm", "label": "CO\u2082",      "rate_unit": "ppm/min"},
    "event":         {"unit": "",    "label": "Event",         "rate_unit": ""},
}

# Maps device-reported unit strings (from registry/birth) to display symbols.
UNIT_DISPLAY: dict[str, str] = {
    "celsius": "\u00b0C",
    "fahrenheit": "\u00b0F",
    "percent": "%",
    "lux": "lux",
    "hpa": "hPa",
    "ppm": "ppm",
}

# Maps common sensor ID prefixes to sensor type strings.
_PREFIX_MAP: dict[str, str] = {
    "temp": "temperature",
    "hum": "humidity",
    "soil": "soil_moisture",
    "contact": "contact",
    "motion": "motion",
    "light": "light_level",
    "co2": "co2",
    "cam": "event",
    "pressure": "pressure",
}


def sensor_unit(sensor_type: str, device_unit: str | None = None) -> str:
    """Resolve display unit for a sensor. Prefers device-reported, falls back to type default."""
    if device_unit:
        return UNIT_DISPLAY.get(device_unit.lower(), device_unit)
    return SENSOR_TYPE_DEFAULTS.get(sensor_type, {}).get("unit", "")


def sensor_label(sensor_type: str) -> str:
    """Human-readable label for a sensor type."""
    return SENSOR_TYPE_DEFAULTS.get(sensor_type, {}).get(
        "label", sensor_type.replace("_", " ").title()
    )


def sensor_rate_unit(sensor_type: str) -> str:
    """Unit for rate-of-change display (e.g., '°C/min')."""
    return SENSOR_TYPE_DEFAULTS.get(sensor_type, {}).get("rate_unit", "/min")


def guess_sensor_type(sensor_id: str) -> str:
    """Best-effort guess of sensor type from sensor ID prefix.

    Example: "temp1" → "temperature", "soil3" → "soil_moisture"
    """
    for prefix, sensor_type in _PREFIX_MAP.items():
        if sensor_id.startswith(prefix):
            return sensor_type
    return sensor_id
