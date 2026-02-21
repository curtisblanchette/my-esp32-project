"""Default scenario — profiles, goals, and MPC configuration.

Actuator targets:
  relay1 = fan, relay2 = exhaust_fan, relay3 = humidifier,
  relay4 = dehumidifier, relay5 = irrigation, relay6 = light
"""

# ── Profile & Goals ─────────────────────────────────────────────────────

PROFILE: dict = {
    "location": "grow-tent",
    "name": "Default Grow Profile",
    "strategy": "balanced",
    "phase": "veg",
}

# Veg Goals — Day/Night split based on Aroya cannabis cultivation science.
# Day = lights-on (06:00–22:00), Night = lights-off (22:00–06:00).
# Night CO2 has no goal (no supplementation needed, ambient 400ppm is fine).
# VPD is lower at night because cooler temps compress the vapor pressure range.

GOALS: list[dict] = [
    # ── Temperature ──────────────────────────────────────────────────
    {"metric": "temp1", "metricType": "sensor", "phase": None,
     "rangeMin": 24.0, "rangeMax": 28.0, "tolerance": 1.0, "priority": 1.0,
     "timeWindow": {"onHour": 6, "offHour": 22}},
    {"metric": "temp1", "metricType": "sensor", "phase": None,
     "rangeMin": 18.0, "rangeMax": 22.0, "tolerance": 1.0, "priority": 1.0,
     "timeWindow": {"onHour": 22, "offHour": 6}},
    # ── Humidity ─────────────────────────────────────────────────────
    {"metric": "hum1", "metricType": "sensor", "phase": None,
     "rangeMin": 55.0, "rangeMax": 70.0, "tolerance": 2.0, "priority": 0.8,
     "timeWindow": {"onHour": 6, "offHour": 22}},
    {"metric": "hum1", "metricType": "sensor", "phase": None,
     "rangeMin": 50.0, "rangeMax": 65.0, "tolerance": 2.0, "priority": 0.8,
     "timeWindow": {"onHour": 22, "offHour": 6}},
    # ── VPD ──────────────────────────────────────────────────────────
    {"metric": "vpd1", "metricType": "sensor", "phase": None,
     "rangeMin": 0.8, "rangeMax": 1.0, "tolerance": 0.1, "priority": 0.9,
     "timeWindow": {"onHour": 6, "offHour": 22}},
    {"metric": "vpd1", "metricType": "sensor", "phase": None,
     "rangeMin": 0.4, "rangeMax": 0.8, "tolerance": 0.1, "priority": 0.9,
     "timeWindow": {"onHour": 22, "offHour": 6}},
    # ── CO2 (day only — no night supplementation) ────────────────────
    {"metric": "co2_1", "metricType": "sensor", "phase": None,
     "rangeMin": 1000.0, "rangeMax": 1300.0, "tolerance": 50.0, "priority": 0.7,
     "timeWindow": {"onHour": 6, "offHour": 22}},
    # ── Soil Moisture ────────────────────────────────────────────────
    {"metric": "soil1", "metricType": "sensor", "phase": None,
     "rangeMin": 45.0, "rangeMax": 65.0, "tolerance": 3.0, "priority": 0.6,
     "timeWindow": {"onHour": 6, "offHour": 22}},
    {"metric": "soil1", "metricType": "sensor", "phase": None,
     "rangeMin": 40.0, "rangeMax": 60.0, "tolerance": 3.0, "priority": 0.6,
     "timeWindow": {"onHour": 22, "offHour": 6}},
]

# Flower Goals — Tighter humidity/VPD for bud rot prevention.
# Higher CO2 during lights-on for heavier flower development.

FLOWER_GOALS: list[dict] = [
    # ── Temperature ──────────────────────────────────────────────────
    {"metric": "temp1", "metricType": "sensor", "phase": "flower",
     "rangeMin": 24.0, "rangeMax": 28.0, "tolerance": 1.0, "priority": 1.0,
     "timeWindow": {"onHour": 6, "offHour": 22}},
    {"metric": "temp1", "metricType": "sensor", "phase": "flower",
     "rangeMin": 18.0, "rangeMax": 22.0, "tolerance": 1.0, "priority": 1.0,
     "timeWindow": {"onHour": 22, "offHour": 6}},
    # ── Humidity ─────────────────────────────────────────────────────
    {"metric": "hum1", "metricType": "sensor", "phase": "flower",
     "rangeMin": 40.0, "rangeMax": 55.0, "tolerance": 2.0, "priority": 0.9,
     "timeWindow": {"onHour": 6, "offHour": 22}},
    {"metric": "hum1", "metricType": "sensor", "phase": "flower",
     "rangeMin": 35.0, "rangeMax": 50.0, "tolerance": 2.0, "priority": 0.9,
     "timeWindow": {"onHour": 22, "offHour": 6}},
    # ── VPD ──────────────────────────────────────────────────────────
    {"metric": "vpd1", "metricType": "sensor", "phase": "flower",
     "rangeMin": 1.0, "rangeMax": 1.4, "tolerance": 0.1, "priority": 1.0,
     "timeWindow": {"onHour": 6, "offHour": 22}},
    {"metric": "vpd1", "metricType": "sensor", "phase": "flower",
     "rangeMin": 0.6, "rangeMax": 1.0, "tolerance": 0.1, "priority": 1.0,
     "timeWindow": {"onHour": 22, "offHour": 6}},
    # ── CO2 (day only) ───────────────────────────────────────────────
    {"metric": "co2_1", "metricType": "sensor", "phase": "flower",
     "rangeMin": 1200.0, "rangeMax": 1500.0, "tolerance": 50.0, "priority": 0.8,
     "timeWindow": {"onHour": 6, "offHour": 22}},
    # ── Soil Moisture ────────────────────────────────────────────────
    {"metric": "soil1", "metricType": "sensor", "phase": "flower",
     "rangeMin": 40.0, "rangeMax": 60.0, "tolerance": 3.0, "priority": 0.6,
     "timeWindow": {"onHour": 6, "offHour": 22}},
    {"metric": "soil1", "metricType": "sensor", "phase": "flower",
     "rangeMin": 35.0, "rangeMax": 55.0, "tolerance": 3.0, "priority": 0.6,
     "timeWindow": {"onHour": 22, "offHour": 6}},
]

# ── MPC Configuration ──────────────────────────────────────────────────
# Default config for the MPC state planner (see simulations/state_planner.py).

MPC_CONFIG: dict = {
    "horizon_minutes": 15.0,
    "control_interval_s": 150.0,
    "w_goal": 1.0,
    "w_energy": 0.05,
    "w_rate": 0.1,
    "method": "SLSQP",
    "light_schedule": {"on_hour": 6, "off_hour": 22},
}

# ── Actuator Control Type Overrides ──────────────────────────────────
# Apply via --variable flag to upgrade select actuators from binary to
# variable (0-10V / PWM) control. Models a facility with EC fans and
# dimmable LED drivers.

VARIABLE_OVERRIDES: dict[str, dict] = {
    "relay2": {"control_type": "variable"},   # EC exhaust fan (0-10V)
    "relay6": {"control_type": "variable"},   # dimmable LED driver
}
