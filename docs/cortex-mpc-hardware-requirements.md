# Cortex MPC Hardware Requirements Specification

## Sensors and Actuators for Grow Room Environment Modeling

---

## Overview

This document defines the sensors and actuators required for Cortex's Model Predictive Control (MPC) optimizer to build and solve an accurate grow room environment model. The room is treated as a thermodynamic box with four coupled state variables. MPC predicts the future trajectory of these states over a 15-60 minute rolling horizon and solves for the optimal actuator sequence to minimize deviation from the target envelope while respecting equipment constraints and energy cost.

---

## State Variables

The grow room environment model tracks four coupled state variables:

| State Variable | Unit | Derived Metrics |
|---------------|------|-----------------|
| **Air temperature** (dry bulb) | °C / °F | Sensible energy balance |
| **Air moisture content** (absolute humidity or dewpoint) | g/kg or °C dewpoint | Latent energy balance. Note: model internally in absolute terms, not RH. RH is a derived quantity that depends on temperature. |
| **CO2 concentration** | ppm | Carbon availability for photosynthesis |
| **Airflow velocity / distribution** | m/s (at canopy) | Boundary layer resistance at leaf surface. Less commonly modeled but affects heat/moisture exchange. |

**VPD (Vapor Pressure Deficit)** — the metric growers optimize for — is calculated from air temperature and humidity, not measured directly. Accurate VPD requires leaf surface temperature, which differs from air temperature by 2-5°F depending on transpiration rate and airflow.

---

## Dominant Disturbances

Three factors dominate grow room thermodynamics. MPC's value over PID comes from predicting these:

### 1. Lights On/Off (Sensible Heat Load)

The single biggest disturbance. A flower room with 100 fixtures × 1,000W = 100kW of heat input switches instantaneously. MPC knows the light schedule in advance — this is a **known future disturbance**, which is exactly what MPC is designed to exploit. Pre-cooling before lights-on is the canonical MPC advantage over reactive PID.

### 2. Transpiration Rate (Latent Load)

Drives humidity dynamics. Depends on VPD, light intensity, substrate moisture availability, and canopy size. Between 90-100% of all irrigation water transpires into the room air. This is the hardest variable to model accurately because it changes as plants grow through their lifecycle. Substrate moisture content (from AROYA TEROS ONE or capacitive sensor) is the best proxy for available-water-to-transpire.

### 3. HVAC System Response (Actuator Dynamics)

How fast does the room actually cool/heat/dehumidify when commanded? Depends on equipment capacity, duct layout, refrigerant charge, and current conditions. MPC learns this from observing supply/return air conditions relative to commands issued. Without this feedback, MPC must assume idealized actuator response.

---

## Sensors

### Tier 1 — Minimum Viable (MPC can function)

These three sensor types provide the three primary state variables plus the dominant disturbance. MPC can build a predictive model from this set alone.

| # | Sensor | Measures | Example Parts | Interface | Update Rate | Est. Cost |
|---|--------|----------|---------------|-----------|-------------|-----------|
| S1 | **Air Temp + RH** | Dry bulb temperature, relative humidity | SHT31 (±0.3°C, ±2% RH) or DHT22 (±0.5°C, ±2-5% RH). SHT31 strongly preferred for accuracy. | I2C → ESP32 → MQTT | ≤ 30 sec | $3-8 |
| S2 | **CO2** | CO2 concentration (ppm) | SCD30 (±30 ppm) or SCD41 (±40 ppm). NDIR principle, self-calibrating. | I2C → ESP32 → MQTT | ≤ 60 sec | $30-50 |
| S3 | **Light State** | Whether lights are on + intensity | Binary: relay sense wire on lighting contactor. Analog: Apogee SQ-520 quantum sensor (PAR, µmol/m²/s). Minimum viable is just the schedule in software. | Digital GPIO or analog → ESP32 → MQTT | Event-driven or ≤ 5 min | $0 (schedule) / $5 (relay sense) / $300+ (PAR sensor) |

**Minimum sensor node BOM per room:** 1× ESP32 + 1× SHT31 + 1× SCD30 + light schedule in software = **~$45-65 in parts**.

Deploy 2-3 nodes per room at different heights/positions for spatial averaging. MPC uses the mean but can detect stratification from the spread.

### Tier 2 — Full Model (significantly better predictions)

These sensors improve MPC prediction accuracy by observing the dominant disturbances and actuator dynamics directly rather than estimating them.

| # | Sensor | Measures | Why MPC Needs It | Example Parts | Interface | Est. Cost |
|---|--------|----------|-----------------|---------------|-----------|-----------|
| S4 | **Substrate Moisture (VWC)** | Volumetric water content in growing media | Transpiration rate is the single biggest latent load driver. VWC tells MPC how much water is available to transpire. Predicts humidity spikes *before* they happen — e.g., irrigation event dumps X gallons into substrate that will transpire over 2-4 hours. **Best obtained via AROYA API integration (TEROS ONE sensor, 3-min updates).** | AROYA TEROS ONE (via REST API) or generic capacitive soil moisture sensor | AROYA API → Cortex poller (3-min) or analog → ESP32 | $0 (AROYA API) / $5-15 (generic capacitive) |
| S5 | **Leaf Surface Temperature** | Actual canopy temperature vs. air temperature | Real VPD uses leaf temp, not air temp. Canopy can be 2-5°F different from air depending on transpiration rate and airflow. Dramatically improves VPD calculation accuracy. | MLX90614 (±0.5°C, IR non-contact) | I2C → ESP32 → MQTT | $8-15 |
| S6 | **Supply Air Temperature** | HVAC discharge air temperature | Lets MPC model the HVAC unit as a real system with lag and capacity limits rather than a black box. Measures what the HVAC is actually delivering. | Thermocouple (K-type) or DS18B20 in duct | 1-Wire or analog → ESP32 → MQTT | $3-10 |
| S7 | **Return Air Temp + RH** | Air conditions at HVAC intake | Combined with supply air temp, gives actual sensible and latent load the system is currently handling. Enables MPC to calculate real-time HVAC capacity utilization. | SHT31 mounted at HVAC return | I2C → ESP32 → MQTT | $3-8 |
| S8 | **PAR Sensor** | Photosynthetically Active Radiation (µmol/m²/s) | Dimming, bulb degradation, and multi-zone lighting mean "lights on" isn't sufficient. PAR directly correlates to photosynthesis rate (CO2 uptake) and heat output. Also measures DLI (Daily Light Integral) for cultivation optimization. | Apogee SQ-520 or similar quantum sensor | Analog → ESP32 → MQTT | $300-500 |
| S9 | **Outdoor Temp + RH** | Ambient conditions outside building envelope | Affects wall conduction loads and any fresh air exchange. Critical for economizer control (free cooling when outdoor air is cooler/drier). **Can be obtained from weather API instead of physical sensor.** | SHT31 (outdoor-rated enclosure) or OpenWeatherMap API | ESP32 → MQTT or API → Cortex | $0 (API) / $15-25 (physical) |
| S10 | **Power Meter (HVAC)** | Actual energy consumption (kW) | Not needed for physics model, but needed for MPC's cost function when optimizing energy spend. Also reveals equipment degradation over time (increasing power for same output = dirty coils, low refrigerant, etc.). | CT clamp (SCT-013) + ADC | Analog → ESP32 → MQTT | $10-20 |

### Sensor Placement Guidelines

```
┌──────────────────────────────────────────────────┐
│                   GROW ROOM                       │
│                                                   │
│   [S8 PAR]        [S5 IR Leaf Temp]              │
│      ↓ (above canopy)   ↓ (aimed at canopy)     │
│   ═══════════════════════════════════  ← Canopy  │
│                                                   │
│   [S1 Temp/RH]  [S1 Temp/RH]  [S1 Temp/RH]     │
│   (Node A)      (Node B)       (Node C)          │
│   canopy height  canopy height  canopy height    │
│                                                   │
│   [S2 CO2] (canopy height, center of room)       │
│   [S4 VWC] (in substrate, via AROYA or direct)   │
│                                                   │
│                                    ┌──────────┐  │
│                                    │ HVAC     │  │
│                              [S7]→ │ Return   │  │
│                                    │          │  │
│                              [S6]← │ Supply   │  │
│                                    │          │  │
│                              [S10] │ Power CT │  │
│                                    └──────────┘  │
│                                                   │
└──────────────────────────────────────────────────┘

[S9 Outdoor Temp/RH] — mounted outside building or via weather API
```

**Key placement rules:**
- Temp/RH sensors at canopy height, not ceiling or floor (stratification makes ceiling readings useless for VPD)
- CO2 sensor at canopy height, center of room (CO2 stratifies — heavier than air, pools at floor)
- IR leaf temp sensor aimed at representative canopy section, not at walls or lights
- Supply/return air sensors inside or immediately adjacent to ductwork
- Minimum 2-3 temp/RH nodes per room for spatial averaging; MPC uses mean, monitors spread for stratification alerts

---

## Actuators

### Tier 1 — Minimum Viable (MPC can solve)

Three actuators for three state variables. MPC can reach target VPD and CO2 but is constrained — it can cool, dehumidify, and inject CO2 but cannot heat, humidify, or modulate airflow independently.

| # | Actuator | Controls | Signal Type | MPC Uses It For | Notes |
|---|----------|----------|-------------|-----------------|-------|
| A1 | **Cooling** (compressor or chilled water valve) | Sensible heat removal | On/off (relay from ESP32) or 0-10V to VFD/valve | Temperature ↓. Also removes moisture as byproduct (condensation on cooling coil). | On/off works but causes oscillation. 0-10V to VFD or modulating valve is strongly preferred for MPC — allows continuous modulation (e.g., "60% cooling" instead of cycling 0%↔100%). |
| A2 | **Dehumidification** (reheat coil or dedicated dehumidifier) | Latent load removal independent of temperature | On/off (relay) or 0-10V / Modbus to HVACD unit | Humidity ↓ without overcooling. **This is the critical actuator.** Without independent dehumidification control, MPC cannot decouple temperature from humidity — every cooling action also removes moisture, and stopping cooling lets moisture build. | In integrated HVACD units (Desert Aire, Altaqua), dehumid mode is a Modbus register or 0-10V input. In split systems, this is a standalone dehumidifier controlled via relay. |
| A3 | **CO2 Injection** (solenoid valve on CO2 tank or burner) | CO2 concentration | On/off relay | CO2 ↑. Binary is sufficient — CO2 dynamics are slow enough that on/off works well. | Safety: CO2 sensor must be present to prevent dangerous accumulation. Typical target: 1,200-1,500 ppm during lights-on. Injection disabled during lights-off (plants don't photosynthesize). |

**Minimum actuator interface BOM:** 3× relay channels on ESP32 (or relay module) = **~$5-10 in parts** for on/off control. Add a 0-10V DAC module ($15-30) for modulating control of cooling.

### Tier 2 — Full Actuator Set (what makes MPC significantly better)

Additional actuators increase MPC's degrees of freedom — more ways to reach the target state means better optimization, lower energy cost, and smoother control.

| # | Actuator | Signal Type | Why It Matters |
|---|----------|-------------|---------------|
| A4 | **Heating** (reheat coil, hot gas bypass, electric heater) | 0-10V or relay | Lets MPC *raise* temperature without waiting for lights. Critical for dark period VPD management — target is often warm + dry at night to maintain transpiration and prevent condensation. Without heating, MPC can only wait for the room to warm up passively. |
| A5 | **Humidification** (ultrasonic or steam humidifier) | On/off relay or 0-10V | Needed in veg rooms and early flower when canopy is small and transpiration is low. Room can get too dry from HVAC operation. Not needed in late flower (transpiration provides all the moisture). |
| A6 | **Fan Speed** (supply air fan VFD) | 0-10V or Modbus | Controls air changes per hour and airflow velocity at canopy. Affects boundary layer resistance (how easily leaf exchanges heat and moisture with air). Higher airflow = faster room mixing = faster response to heating/cooling commands. MPC can reduce fan speed during lights-off for energy savings if room is stable. |
| A7 | **Damper Position** (fresh air / recirculation) | 0-10V to damper actuator | Controls whether HVAC recirculates room air (preserving CO2) or brings in outdoor air. **Economizer mode:** if outdoor air is cooler and/or drier than room air, opening the damper provides free cooling/dehumidification. MPC can exploit weather forecasts + outdoor sensor to schedule economizer operation. Most grows run 100% recirculation to preserve CO2 — damper control enables MPC to trade CO2 cost vs. energy cost. |
| A8 | **Irrigation Trigger** | Digital signal to OpenSprinkler / solenoid valve | Crosses into AROYA territory, but irrigation is the *input* to the transpiration model. If MPC controls or is aware of irrigation timing, it can pre-adjust dehumidification before the latent load spike. Even read-only awareness (from AROYA API polling) is valuable. |

### Actuator Interface Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CORTEX GATEWAY                            │
│                                                              │
│  MPC Optimizer → Action Sequence                             │
│       │                                                      │
│       ├─── MQTT ──→ ESP32 Relay Board                        │
│       │              ├─ A1: Cooling (on/off fallback)        │
│       │              ├─ A2: Dehumid (on/off fallback)        │
│       │              ├─ A3: CO2 solenoid                     │
│       │              ├─ A4: Heater (on/off)                  │
│       │              └─ A5: Humidifier (on/off)              │
│       │                                                      │
│       ├─── Modbus RTU (RS-485) ──→ HVACD Unit               │
│       │    via USB-to-RS485 adapter   ├─ Cooling mode/speed  │
│       │    on Cortex Gateway          ├─ Dehumid mode/speed  │
│       │                               ├─ Heating mode/speed  │
│       │                               └─ Fan speed           │
│       │                                                      │
│       ├─── 0-10V DAC ──→ VFDs / Valve Actuators             │
│       │    (MCP4725 I2C DAC              ├─ A1: Compressor   │
│       │     or dedicated 0-10V module)   ├─ A6: Supply fan   │
│       │                                  └─ A7: Damper       │
│       │                                                      │
│       └─── HTTP/MQTT ──→ OpenSprinkler                       │
│                           └─ A8: Irrigation valves           │
│                              (read-only awareness or         │
│                               active control)                │
└─────────────────────────────────────────────────────────────┘
```

**Signal priority for commercial HVACD integration:**
1. **Modbus RTU** — preferred. Single RS-485 bus controls all functions of the HVACD unit. Two wires, up to 30 devices. Most purpose-built grow HVAC units (Desert Aire GrowAire, Altaqua, Cultiva) support Modbus.
2. **0-10V analog** — fallback. Universal, works with any VFD or valve actuator. Requires one DAC channel per actuator. No feedback (one-way signal).
3. **Relay (on/off)** — last resort. Works but limits MPC to bang-bang control. Acceptable for CO2 solenoids and simple humidifiers. Not ideal for cooling/heating where modulation matters.

---

## Build Priority Sequence

Ranked by impact on MPC model accuracy per dollar and implementation effort:

| Priority | Item | Type | Impact on MPC | Implementation Effort | Status |
|----------|------|------|---------------|----------------------|--------|
| 1 | Air Temp + RH (SHT31) | Sensor | ★★★★★ Core state variable | Low — I2C to existing ESP32 | ✅ Exists (DHT22). Upgrade to SHT31 recommended. |
| 2 | CO2 (SCD30) | Sensor | ★★★★★ Core state variable | Low — I2C to existing ESP32 | ✅ Exists |
| 3 | Light Schedule | Sensor (software) | ★★★★★ Dominant disturbance, known in advance | None — configure in Cortex | 🔲 To implement |
| 4 | Cooling Actuator | Actuator | ★★★★★ Primary temperature control | Medium — relay (easy) or 0-10V/Modbus (moderate) | 🔲 To implement |
| 5 | Dehumidification Actuator | Actuator | ★★★★★ Independent humidity control | Medium — relay or Modbus to HVACD | 🔲 To implement |
| 6 | CO2 Solenoid | Actuator | ★★★★☆ CO2 control | Low — relay | 🔲 To implement |
| 7 | Substrate Moisture (VWC) | Sensor | ★★★★☆ Transpiration prediction | Low — AROYA API integration (software only) | 🔲 To implement |
| 8 | Leaf Surface Temperature | Sensor | ★★★★☆ Accurate VPD calculation | Low — MLX90614, I2C, $10 | 🔲 To implement |
| 9 | Supply/Return Air Sensors | Sensor | ★★★☆☆ HVAC dynamics model | Low — SHT31 + thermocouple in ducts | 🔲 To implement |
| 10 | Heating Actuator | Actuator | ★★★☆☆ Dark period VPD control | Medium — relay or 0-10V to reheat coil | 🔲 To implement |
| 11 | Humidifier Actuator | Actuator | ★★★☆☆ Veg room humidity addition | Low — relay to humidifier | 🔲 To implement |
| 12 | Fan Speed Control | Actuator | ★★☆☆☆ Airflow modulation | Medium — 0-10V to supply fan VFD | 🔲 To implement |
| 13 | PAR Sensor | Sensor | ★★☆☆☆ Precise light intensity | Low (wiring) but expensive ($300+) | 🔲 Optional |
| 14 | Outdoor Temp/RH | Sensor | ★★☆☆☆ Envelope loads, economizer | None — weather API | 🔲 To implement |
| 15 | Power Meter | Sensor | ★★☆☆☆ Energy cost optimization | Low — CT clamp + ADC | 🔲 Optional |
| 16 | Damper Position | Actuator | ★☆☆☆☆ Economizer / fresh air | Medium — 0-10V to damper actuator | 🔲 Optional |

**Items 1-6:** Working MPC. Estimated hardware cost per room: **$80-150** (ESP32 nodes + relays + CO2 sensor).

**Items 7-12:** Significantly better MPC. Estimated additional cost: **$40-80** (leaf temp sensor, duct sensors, AROYA API is free).

**Items 13-16:** Refinements. Add when optimizing for energy cost or operating economizer cycles. **$310-550** additional.

---

## MPC Model Structure

For reference, here is how the sensor and actuator data maps to the MPC optimization problem:

### State Vector (observed by sensors)

```
x = [T_air, w_air, CO2, T_leaf, VWC_substrate]
```

Where:
- `T_air` = air temperature (S1)
- `w_air` = absolute humidity / moisture content (derived from S1 temp + RH)
- `CO2` = CO2 concentration in ppm (S2)
- `T_leaf` = leaf surface temperature (S5, or estimated from T_air if unavailable)
- `VWC_substrate` = volumetric water content (S4, or estimated from irrigation schedule if unavailable)

### Control Vector (commanded to actuators)

```
u = [Q_cool, Q_dehum, Q_heat, Q_humidify, m_CO2, v_fan, d_damper]
```

Where:
- `Q_cool` = cooling power (A1, 0-100%)
- `Q_dehum` = dehumidification power (A2, 0-100%)
- `Q_heat` = heating power (A4, 0-100%)
- `Q_humidify` = humidification rate (A5, 0-100%)
- `m_CO2` = CO2 injection rate (A3, on/off)
- `v_fan` = fan speed (A6, 0-100%)
- `d_damper` = damper position (A7, 0-100% open)

### Disturbance Vector (predicted, not controlled)

```
d = [Q_lights, E_transpiration, T_outdoor, RH_outdoor, Q_irrigation]
```

Where:
- `Q_lights` = heat from lighting (from S3 light schedule — **known in advance**)
- `E_transpiration` = plant transpiration rate (estimated from VPD × canopy model × VWC)
- `T_outdoor` = outdoor temperature (S9 or weather API — **forecastable**)
- `RH_outdoor` = outdoor humidity (S9 or weather API — **forecastable**)
- `Q_irrigation` = irrigation events (from AROYA API or S4 VWC step changes — **known or predictable**)

### Cost Function (what MPC minimizes)

```
J = Σ over horizon [
    w1 × (VPD - VPD_target)²           # VPD tracking (primary objective)
  + w2 × (T_air - T_target)²            # Temperature tracking
  + w3 × (CO2 - CO2_target)²            # CO2 tracking
  + w4 × energy_cost(u)                 # Energy minimization
  + w5 × Σ(Δu²)                        # Actuator smoothness (prevent rapid cycling)
]
```

Subject to:
- Equipment capacity constraints (max cooling, max dehumid, etc.)
- Rate-of-change limits (compressor can't cycle faster than X per hour)
- Safety bounds (temperature must stay within [min, max], CO2 must stay below safety limit)
- Phase-specific targets (VPD_target, T_target, CO2_target vary by growth phase and light state)

---

## Integration Notes

### AROYA API Data Mapping

Data available from AROYA REST API (`https://api.aroya.io/public_api/`) that feeds MPC model:

| AROYA Data Point | Maps To | MPC Use |
|-----------------|---------|---------|
| TEROS ONE: VWC | S4 (substrate moisture) | Transpiration prediction |
| TEROS ONE: Pore Water EC | — | Nutrient stress indicator (affects transpiration rate) |
| TEROS ONE: Substrate Temp | — | Root zone health indicator |
| CLIMATE ONE: Air Temp | S1 (cross-validation) | Validate ESP32 readings, detect sensor drift |
| CLIMATE ONE: RH | S1 (cross-validation) | Validate ESP32 readings |
| CLIMATE ONE: CO2 | S2 (cross-validation) | Validate ESP32 readings |
| CLIMATE ONE: VPD | — | Pre-calculated VPD for comparison |
| CLIMATE ONE: Light (DLI/PAR) | S3/S8 | Light intensity data |
| Drip sensor: Feed volume | Irrigation events | Predict upcoming transpiration load |
| Drain sensor: Runoff EC + volume | — | Nutrient uptake indicator |

**Polling interval:** 3 minutes (matches AROYA sensor update rate).
**Latency consideration:** AROYA data is 3 minutes old minimum. ESP32 sensors provide sub-second readings. MPC uses ESP32 for real-time control loop and AROYA for slower-moving substrate/irrigation data.

### Commercial HVACD Modbus Register Examples

Typical Modbus register map for purpose-built grow HVACD units (varies by manufacturer — confirm with specific equipment manual):

| Register | Function | Read/Write | Data Type |
|----------|----------|-----------|-----------|
| 40001 | Operating Mode (off/cool/heat/dehum/auto) | R/W | INT16 |
| 40002 | Cooling Setpoint (°F × 10) | R/W | INT16 |
| 40003 | Heating Setpoint (°F × 10) | R/W | INT16 |
| 40004 | Dehumidification Setpoint (% RH × 10) | R/W | INT16 |
| 40005 | Fan Speed (0-100%) | R/W | INT16 |
| 30001 | Supply Air Temperature (°F × 10) | R | INT16 |
| 30002 | Return Air Temperature (°F × 10) | R | INT16 |
| 30003 | Return Air Humidity (% RH × 10) | R | INT16 |
| 30004 | Compressor Status (0=off, 1=stage1, 2=stage2) | R | INT16 |
| 30005 | Alarm Status (bitmask) | R | INT16 |

**Note:** Register addresses are illustrative. Actual addresses must be obtained from the specific HVACD manufacturer's Modbus documentation. Desert Aire, Altaqua, and Cultiva all publish Modbus maps for their commercial grow units.

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-02-19 | Initial draft — sensor/actuator requirements, MPC model structure, build priority |
