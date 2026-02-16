# Generic Sensor/Actuator Support for Rule Generation + Chat Prompts

## Context

The rule generation pipeline, chat system prompt, and rule form UI are hardcoded to assume `temp`/`humidity` sensors and `relay` actuators. This prevents the system from working with diverse setups:

- **Grow room**: temp, humidity, soil moisture sensors + drip watering, exhaust, fan, light actuators
- **Garage/security**: magnetic contact sensor, ML video detection + garage opener (momentary), lights
- **Nursery/server room/etc.**: varying combinations of sensor/actuator primitives

The device firmware already sends structured `{id, type, unit}` sensor metadata in birth messages and the SQLite `capabilities` JSON stores it — but the `Sensor` dataclass drops `unit` during deserialization. All prompt formatting, rule display, and form dropdowns assume only two sensor types.

**Scope**: Rule generation pipeline + chat prompts + rule form UI only. NOT telemetry storage, redis, data_reader, analysis, or frontend gauges.

---

## Files to Modify

| File | Change |
|------|--------|
| `apps/cortex/src/services/sqlite_client.py` | Add `unit` field to `Sensor` dataclass, preserve in serialization |
| `apps/cortex/src/services/sensor_meta.py` | **New** — sensor type → unit/label defaults + resolution helpers |
| `apps/cortex/src/services/rule_generator.py` | Generic readings, baseline units, multi-domain system prompt |
| `apps/cortex/src/services/ollama_client.py` | Generic reading formatting in prompts, remove hardcoded ranges |
| `apps/cortex/src/services/intent_executor.py` | Generic sensor query lookup + rule display formatting |
| `apps/web/src/api.ts` | Add `unit?` to sensor type, add `guessSensorUnit()` utility |
| `apps/web/src/components/RuleFormModal.tsx` | Dynamic sensors/actuators from device capabilities |
| `apps/web/src/components/NerveCenterRules.tsx` | Generic unit resolution in `conditionSummary()` |
| `apps/web/src/pages/NerveCenter.tsx` | Thread `devices` prop to `NerveCenterRules` |
| `apps/cortex/tests/test_sensor_meta.py` | **New** — sensor metadata utility tests |

---

## Plan

### 1. Add `unit` to `Sensor` dataclass
**File**: `apps/cortex/src/services/sqlite_client.py`

The `unit` field is already in the birth payload JSON stored in SQLite — just not deserialized.

- Add `unit: str | None = None` to `Sensor` (line 79)
- Update `_row_to_device()` (line 1224): `Sensor(id=s["id"], type=s["type"], name=s.get("name"), unit=s.get("unit"))`
- Update `to_dict()` (line 114): include `**({"unit": s.unit} if s.unit else {})` in sensor serialization

### 2. Create sensor metadata utility
**New file**: `apps/cortex/src/services/sensor_meta.py`

```python
SENSOR_TYPE_DEFAULTS = {
    "temperature":   {"unit": "°C",  "label": "Temperature",   "rate_unit": "°C/min"},
    "humidity":      {"unit": "%",   "label": "Humidity",      "rate_unit": "%/min"},
    "soil_moisture": {"unit": "%",   "label": "Soil Moisture", "rate_unit": "%/min"},
    "contact":       {"unit": "",    "label": "Contact",       "rate_unit": ""},
    "motion":        {"unit": "",    "label": "Motion",        "rate_unit": ""},
    "light_level":   {"unit": "lux", "label": "Light Level",   "rate_unit": "lux/min"},
    "pressure":      {"unit": "hPa", "label": "Pressure",      "rate_unit": "hPa/min"},
    "co2":           {"unit": "ppm", "label": "CO₂",           "rate_unit": "ppm/min"},
    "event":         {"unit": "",    "label": "Event",         "rate_unit": ""},
}

UNIT_DISPLAY = {"celsius": "°C", "fahrenheit": "°F", "percent": "%", ...}

def sensor_unit(sensor_type, device_unit=None) -> str
def sensor_label(sensor_type) -> str
def sensor_rate_unit(sensor_type) -> str
def guess_sensor_type(sensor_id) -> str  # prefix-based: "temp1" → "temperature"
```

`guess_sensor_type()` maps common ID prefixes to types:
- `temp` → `temperature`, `hum` → `humidity`, `soil` → `soil_moisture`
- `contact` → `contact`, `motion` → `motion`, `light` → `light_level`
- `co2` → `co2`, `cam` → `event`

### 3. Update `RULE_GENERATION_SYSTEM_PROMPT` — multi-domain examples
**File**: `apps/cortex/src/services/rule_generator.py`

Add to the existing guidelines:
- `"pulse"` action for momentary actuators (garage doors, buzzers)
- Binary/contact sensors use `==` with `0`/`1`
- Remove temp-specific threshold example, add multi-domain examples:

```
Example rule sets by domain:

Grow room (temp + humidity + soil + relays):
- temp1 > 28 for 60s → exhaust1 ON (ventilate)
- soil1 < 60 for 120s → drip1 ON (water dry soil)
- hum1 > 55 trending rising → exhaust1 ON (preemptive dehumidify)
- light1 ON between 06:00-18:00 (veg photoperiod), OFF otherwise

Garage/security (contact + camera + momentary):
- contact1 == 1 for 600s → garage1 pulse (auto-close after 10 min)
- contact1 == 1 for 0s → lights1 ON (lights on when door opens)
- contact1 == 0 for 120s → lights1 OFF (lights off 2 min after close)
```

### 4. Update `_build_generation_context()` — generic readings
**File**: `apps/cortex/src/services/rule_generator.py`

**Current** (lines 191-200): checks only `"temp"` and `"humidity"` keys.

**New**: Iterate all keys in the reading dict, skip metadata keys (`updatedAt`, `sourceTopic`, `deviceId`, `sourceIp`), resolve units from device capabilities via `sensor_meta`.

Add `_resolve_reading_unit(key, sensor_map)` helper that:
1. Direct-matches reading key to sensor ID
2. Falls back to legacy map (`"temp"` → `"temp1"`, `"humidity"` → `"hum1"`)
3. Falls back to prefix-guessing

### 5. Update `_format_generation_prompt()` — generic baseline units
**File**: `apps/cortex/src/services/rule_generator.py`

**Current** (line 259): `unit = "°C" if "temp" in sensor else "%"`

**New**: `unit = sensor_unit(sensor)` — the baseline metric names match sensor type strings.

### 6. Update `build_system_prompt()` — generic readings
**File**: `apps/cortex/src/services/ollama_client.py`

**Current** (line 422): `f"Current readings: temperature={reading.get('temp', 0):.1f}°C, humidity={reading.get('humidity', 0):.1f}%"`

**New**: Iterate all reading keys generically (same pattern as step 4). Build sensor map from device capabilities for unit resolution.

### 7. Update `_build_prompt()` — generic units in trend/baseline/forecast
**File**: `apps/cortex/src/services/ollama_client.py`

Replace hardcoded unit strings:
- Line 128: `unit = "°C/min" if "temp" in sensor else "%/min"` → `sensor_rate_unit(sensor)`
- Line 153: `unit = "°C" if metric == "temperature" else "%"` → `sensor_unit(metric)`
- Line 166: `unit = "°C" if "temp" in sensor else "%"` → `sensor_unit(sensor)`
- Lines 203-206: Remove hardcoded comfort ranges, use generic phrasing:
  ```
  Are any sensor values outside normal ranges (use baselines and goals if available)?
  Are there concerning trends or deviations from baseline?
  ```

### 8. Update `SYSTEM_PROMPT` — remove hardcoded relay1
**File**: `apps/cortex/src/services/ollama_client.py`

This is the decision engine escalation prompt (line 32-48). Remove the `- relay1: A switch` line. Replace with generic instructions since the actual device context is in the prompt body.

### 9. Update query intent — generic sensor lookup
**File**: `apps/cortex/src/services/intent_executor.py`

**Current** (lines 137-140): Only handles `"temp1"` → `"temp"` and `"hum1"` → `"humidity"`.

**New**: Try direct key match first (for future generic telemetry), then legacy mapping, then fall through.

### 10. Update `_format_proposed_rules()` — generic labels/units
**File**: `apps/cortex/src/services/intent_executor.py`

**Current** (lines 409-410): `sensor_label = "Temperature" if "temp" in sensor else "Humidity"...`

**New**: Use `sensor_meta.guess_sensor_type()` + `sensor_label()` + `sensor_unit()`.

### 11. Update `RuleFormModal.tsx` — dynamic sensors/actuators
**File**: `apps/web/src/components/RuleFormModal.tsx`

- Add `devices: Device[]` to `RuleFormModalProps`
- Derive `sensors` and `targets` from device capabilities with `useMemo`:
  ```ts
  const sensors = useMemo(() => {
    const ids = new Set<string>();
    for (const d of devices) for (const s of d.capabilities.sensors) ids.add(s.id);
    return [...ids].sort();
  }, [devices]);
  ```
- Always include `"notification_center"` in targets (virtual actuator)
- Default sensor/target to first available instead of hardcoded
- Remove `const SENSORS` and `const TARGETS` constants

### 12. Thread `devices` prop through NerveCenter → Rules → Modal
**Files**: `NerveCenter.tsx`, `NerveCenterRules.tsx`

- `NerveCenter.tsx` line 115: pass `devices={devices}` to `NerveCenterRules`
- `NerveCenterRules.tsx`: add `devices: Device[]` to `RulesProps`, pass to `RuleFormModal`

### 13. Update `conditionSummary()` — generic units
**File**: `apps/web/src/components/NerveCenterRules.tsx`

**Current** (line 15): `c.sensor.startsWith("temp") ? "°C" : "%"`

**New**: Add `guessSensorUnit()` frontend utility to `api.ts`:
```ts
export function guessSensorUnit(sensorId: string): string {
  if (sensorId.startsWith("temp")) return "°C";
  if (sensorId.startsWith("hum") || sensorId.startsWith("soil")) return "%";
  if (sensorId.startsWith("light")) return "lux";
  if (sensorId.startsWith("co2")) return "ppm";
  if (sensorId.startsWith("pressure")) return "hPa";
  return "";
}
```

Use in `conditionSummary()`: `const unit = guessSensorUnit(c.sensor);`

### 14. Update frontend `Device` type
**File**: `apps/web/src/api.ts`

Add `unit?: string` to the sensor type in `Device.capabilities.sensors`.

### 15. Tests

**New file**: `apps/cortex/tests/test_sensor_meta.py`
- `sensor_unit()` with device-reported unit, type-based fallback, unknown type
- `sensor_label()` known + unknown types
- `sensor_rate_unit()` known types
- `guess_sensor_type()` various prefixes + unknown
- `UNIT_DISPLAY` mapping

**Update**: `apps/cortex/tests/test_rule_generator.py`
- Add test with diverse sensor types (soil_moisture, contact)
- Add test verifying generic reading formatting
- Add test verifying baseline units use sensor_meta

---

## Legacy Bridge: `_LEGACY_MAP`

The current MQTT handler stores readings as `{"temp": value, "humidity": value}` — flat keys, not sensor IDs. Until the telemetry pipeline is also made generic, we need a `_LEGACY_MAP = {"temp": "temp1", "humidity": "hum1"}` bridge in reading unit resolution. This is intentionally forward-compatible: when telemetry goes generic (reading keys = sensor IDs), the direct match path activates automatically.

## Verification
1. `cd apps/cortex && pytest tests/ -m "not e2e"` — all tests pass
2. `cd apps/web && npx tsc --noEmit` — frontend type-checks
3. Rule form modal shows sensors/actuators from actual device capabilities
4. `conditionSummary()` shows correct units for soil_moisture, contact, etc.
5. Chat rule generation prompt includes domain-specific examples
6. `_format_proposed_rules()` labels soil sensors as "Soil Moisture", contact as "Contact", etc.
