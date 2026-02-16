# Cross-Device Rule Generation Awareness

## Context

The rule generator (`rule_generator.py`) filters devices to only those at the target location, so the LLM never sees devices at other locations. This makes cross-device rule generation impossible — e.g., "turn on light 1 in garage when humidity in grow room is trending down". The decision engine already supports `scope` and `target_scope` fields for cross-device rules, but the rule generator doesn't expose them.

## Files to Modify

1. **`apps/cortex/src/services/rule_generator.py`** — Core changes (system prompt, context, validation, cleanup)
2. **`apps/cortex/src/services/intent_executor.py`** — Display scope/target_scope in proposed rules
3. **`apps/cortex/tests/test_rule_generator.py`** — New cross-device test coverage

## Changes

### 1. Update `RULE_GENERATION_SYSTEM_PROMPT` (rule_generator.py:43-102)

- Add `scope` and `target_scope` to the JSON schema
- Update guidelines: explain when to use scope/target_scope vs local rules
- Add cross-location rule examples alongside existing grow-room/garage examples
- Note: `target_scope` does NOT support `"any"` — only `"self"`, `"all"`, or `"<device_id>"`

### 2. Update `_build_generation_context()` (rule_generator.py:201-283)

- Keep existing location-filtered `devices` as primary
- Collect ALL other devices into `other_devices`, grouped by location
- Build `other_device_sections: dict[str, list[str]]` (location → device descriptions)
- Track global sets: `all_sensor_ids`, `all_actuator_ids`, `all_device_ids`
- Include baselines from other-location devices too
- Extract device-description formatting into `_format_device_section()` helper to avoid duplication
- Add new keys to returned context dict

### 3. Update `_format_generation_prompt()` (rule_generator.py:285-311)

After "Devices at this location:" section, add:
```
Other devices (available for cross-device rules via scope/target_scope):
  Location: garage
    esp32-garage (Garage Controller) [online]
      Sensors: ...
      Actuators: ...
```

### 4. Update `_format_refinement_prompt()` (rule_generator.py:313-337)

Same other-devices section as generation prompt.

### 5. Update `_validate_generated_rule()` (rule_generator.py:392-447)

- Validate `scope` value: must be in `{"self", "any", "all"}` or a valid device ID
- Validate `target_scope` value: must be in `{"self", "all"}` or a valid device ID (no `"any"`)
- When `scope != "self"`: validate sensor against `all_sensor_ids` instead of location-only
- When `target_scope != "self"`: validate actuator against `all_actuator_ids` instead of location-only

### 6. Update `_clean_rule()` (rule_generator.py:449-472)

- Strip `scope` when `None` or `"self"` (default)
- Strip `target_scope` when `None` or `"self"` (default)

### 7. Update `_parse_and_validate` error message (rule_generator.py:369-384)

Show system-wide sensor/actuator IDs in addition to location-only when they differ.

### 8. Update `_format_proposed_rules()` (intent_executor.py:396-437)

- After condition parts, append scope label (e.g., "(any device)", "(device: esp32-garage)")
- After action string, append target_scope label (e.g., "(all devices)", "(device: esp32-garage)")

### 9. Tests (test_rule_generator.py)

| Test | Validates |
|------|-----------|
| `test_context_includes_other_location_devices` | `other_devices` populated, `all_sensor_ids` spans locations |
| `test_prompt_shows_other_devices` | Generation prompt includes "Other devices" section |
| `test_validate_scope_any_cross_device_sensor` | `scope: "any"` + remote sensor passes |
| `test_validate_scope_device_id_cross_device` | `scope: "<device_id>"` + remote sensor passes |
| `test_validate_target_scope_all` | `target_scope: "all"` + remote actuator passes |
| `test_validate_target_scope_device_id` | `target_scope: "<device_id>"` + remote actuator passes |
| `test_validate_rejects_invalid_scope` | Unknown device ID in scope fails |
| `test_validate_rejects_target_scope_any` | `target_scope: "any"` fails (not supported) |
| `test_validate_scope_self_enforces_location` | `scope: "self"` + remote sensor fails |
| `test_clean_strips_default_scope` | `scope: "self"` removed from output |
| `test_clean_preserves_non_default_scope` | `scope: "any"` kept in output |
| `test_context_baselines_include_other_devices` | Baselines from other locations included |

## Verification

1. Run existing tests to confirm no regressions: `cd apps/cortex && pytest tests/test_rule_generator.py -v`
2. Run new cross-device tests
3. Run full unit suite: `cd apps/cortex && pytest tests/ -m "not e2e"`