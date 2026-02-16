"""Grow tent automation rules for simulation.

Same dict format as SQLite/YAML rules — loaded directly into DecisionEngine.
Actuator targets map to relay IDs:
  relay1 = fan, relay2 = exhaust_fan, relay3 = humidifier,
  relay4 = dehumidifier, relay5 = irrigation, relay6 = light
"""

GROW_TENT_RULES: list[dict] = [
    # ── Temperature Control ───────────────────────────────────────────
    {
        "name": "high_temp_exhaust_on",
        "description": "Turn on exhaust fan when temperature exceeds 28°C",
        "condition": {
            "sensor": "temp1",
            "operator": ">",
            "threshold": 28,
            "duration_seconds": 60,
        },
        "action": {
            "target": "relay2",
            "action": "set",
            "value": True,
            "reason": "Temperature exceeded 28°C — exhausting hot air",
        },
    },
    {
        "name": "temp_restored_exhaust_off",
        "description": "Turn off exhaust fan when temperature drops below 25°C",
        "condition": {
            "sensor": "temp1",
            "operator": "<",
            "threshold": 25,
            "duration_seconds": 60,
        },
        "action": {
            "target": "relay2",
            "action": "set",
            "value": False,
            "reason": "Temperature restored below 25°C",
        },
    },
    {
        "name": "mild_heat_fan_on",
        "description": "Turn on circulation fan when temperature exceeds 26°C",
        "condition": {
            "sensor": "temp1",
            "operator": ">",
            "threshold": 26,
            "duration_seconds": 30,
        },
        "action": {
            "target": "relay1",
            "action": "set",
            "value": True,
            "reason": "Temperature above 26°C — circulating air",
        },
    },
    {
        "name": "mild_heat_fan_off",
        "description": "Turn off circulation fan when temperature drops below 25°C",
        "condition": {
            "sensor": "temp1",
            "operator": "<",
            "threshold": 25,
            "duration_seconds": 30,
        },
        "action": {
            "target": "relay1",
            "action": "set",
            "value": False,
            "reason": "Temperature restored below 25°C — fan off",
        },
    },
    # ── Humidity Control ──────────────────────────────────────────────
    {
        "name": "high_humidity_dehumidifier_on",
        "description": "Turn on dehumidifier when humidity exceeds 65%",
        "condition": {
            "sensor": "hum1",
            "operator": ">",
            "threshold": 65,
            "duration_seconds": 60,
        },
        "action": {
            "target": "relay4",
            "action": "set",
            "value": True,
            "reason": "Humidity exceeded 65% — dehumidifying",
        },
    },
    {
        "name": "humidity_restored_dehumidifier_off",
        "description": "Turn off dehumidifier when humidity drops below 55%",
        "condition": {
            "sensor": "hum1",
            "operator": "<",
            "threshold": 55,
            "duration_seconds": 60,
        },
        "action": {
            "target": "relay4",
            "action": "set",
            "value": False,
            "reason": "Humidity restored below 55%",
        },
    },
    {
        "name": "low_humidity_humidifier_on",
        "description": "Turn on humidifier when humidity drops below 40%",
        "condition": {
            "sensor": "hum1",
            "operator": "<",
            "threshold": 40,
            "duration_seconds": 60,
        },
        "action": {
            "target": "relay3",
            "action": "set",
            "value": True,
            "reason": "Humidity below 40% — humidifying",
        },
    },
    {
        "name": "humidity_ok_humidifier_off",
        "description": "Turn off humidifier when humidity rises above 50%",
        "condition": {
            "sensor": "hum1",
            "operator": ">",
            "threshold": 50,
            "duration_seconds": 60,
        },
        "action": {
            "target": "relay3",
            "action": "set",
            "value": False,
            "reason": "Humidity restored above 50%",
        },
    },
    # ── Soil Moisture / Irrigation ────────────────────────────────────
    {
        "name": "dry_soil_irrigate",
        "description": "Start irrigation when soil moisture drops below 40%",
        "condition": {
            "sensor": "soil1",
            "operator": "<",
            "threshold": 40,
            "duration_seconds": 120,
        },
        "action": {
            "target": "relay5",
            "action": "set",
            "value": True,
            "reason": "Soil moisture below 40% — irrigating",
        },
    },
    {
        "name": "soil_saturated_stop",
        "description": "Stop irrigation when soil moisture exceeds 75%",
        "condition": {
            "sensor": "soil1",
            "operator": ">",
            "threshold": 75,
            "duration_seconds": 60,
        },
        "action": {
            "target": "relay5",
            "action": "set",
            "value": False,
            "reason": "Soil saturated above 75% — stopping irrigation",
        },
    },
    # ── Lighting (Time-of-Day) ────────────────────────────────────────
    {
        "name": "lights_on_daytime",
        "description": "Turn on grow lights during daytime hours",
        "condition": {
            "sensor": "light1",
            "operator": "<",
            "threshold": 100,
            "duration_seconds": 0,
            "time_of_day": {"after": "06:00", "before": "21:59"},
        },
        "action": {
            "target": "relay6",
            "action": "set",
            "value": True,
            "reason": "Daytime — grow lights on",
        },
    },
    {
        "name": "lights_off_night",
        "description": "Turn off grow lights during nighttime hours",
        "condition": {
            "sensor": "light1",
            "operator": ">",
            "threshold": 100,
            "duration_seconds": 0,
            "time_of_day": {"after": "22:00", "before": "05:59"},
        },
        "action": {
            "target": "relay6",
            "action": "set",
            "value": False,
            "reason": "Nighttime — grow lights off",
        },
    },
]
