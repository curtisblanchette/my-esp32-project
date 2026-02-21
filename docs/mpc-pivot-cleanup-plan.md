# MPC Pivot: Remove Rules-Based DecisionEngine

## Context

The project is pivoting from a rules-based control plane (DecisionEngine with hysteresis/bang-bang/diurnal rules) to MPC-only (Model Predictive Control via `MPCPlanner` + `PhysicsEngine`). All rules-based code — services, tests, simulation paths, CLI flags, API endpoints, and SQLite tables — is now dead weight and should be removed.

**Scope:** Full production + simulation cleanup. ~31 files deleted, ~12 files refactored, ~6,000-7,000 lines removed.

---

## Phase 1: Delete test files (18 files)

No production code depends on these. Safe to delete without cascading breakage.

**Delete:**
- `tests/test_decision_engine.py` (957 lines)
- `tests/test_rule_advisor.py` (1554 lines)
- `tests/test_outcome_tracker.py` (560 lines)
- `tests/test_rules_crud.py`
- `tests/test_suggestion_cleanup.py` (165 lines)
- `tests/test_cross_device_rules.py`
- `tests/test_forecaster.py`
- `tests/test_cortex_memory.py`
- `tests/test_effect_tracker.py`
- `tests/test_impact_estimator.py`
- `tests/test_goal_aware_decisions.py`
- `tests/test_recovery_tracker.py`
- `tests/test_rule_migration.py`
- `tests/test_coordinator.py`
- `tests/test_simulation_adaptive.py`
- `tests/test_simulation_control.py`
- `tests/test_simulation_diurnal.py`
- `tests/test_simulation_effects.py`
- `tests/test_simulation_goal_aware.py`

**Verify:** `pytest tests/test_simulation_mpc.py tests/test_fast_physics.py tests/test_fast_physics_mpc.py tests/test_state_planner.py tests/test_simulation_multi_day.py tests/test_simulation_physics.py -v`

---

## Phase 2: Delete service files (12 files)

Rules-only services with zero MPC usage.

**Delete:**
- `src/services/decision_engine.py` (842 lines)
- `src/services/rule_advisor.py`
- `src/services/outcome_tracker.py`
- `src/services/cortex_memory.py`
- `src/services/forecaster.py`
- `src/services/coordinator.py`
- `src/services/impact_estimator.py`
- `src/services/recovery_tracker.py`
- `src/services/effect_tracker.py`
- `src/services/migrate_rules.py`
- `src/services/rule_generator.py` (generates rules for DecisionEngine)

**Keep but strip rules fields from:** `src/services/chat_session.py`
- Remove `proposed_rules` field from `ChatSession` dataclass
- Remove `set_proposed_rules()`, `get_proposed_rules()`, `clear_proposed_rules()` methods
- Keep `get_or_create()`, `add_message()`, `get_conversation_context()` (needed for multi-turn chat)

**DO NOT run tests yet** — Phase 3 fixes broken imports.

---

## Phase 3: Refactor production backend (5 files)

### 3A: `src/main.py` — Make MPC the default

**Remove imports:** `DecisionEngine`, `_rules_from_dicts`, `RuleAdvisor`, `OutcomeTracker`, `CortexMemory`, `Coordinator`, `EffectTracker`, `RecoveryTracker`, `RuleGenerator`, `ChatSessionStore` (move session store to chat route if needed)

**Remove from `__init__`:** `self.engine`, `self._memory`, `self._outcome_tracker`, `self._effect_tracker`, `self._recovery_tracker`, `self._coordinator`, `self._context_cache*`, `self._pending_llm_analysis`, `self._rule_generator`, `self._chat_session_store`

**Remove from `start()`:**
- Rules seeding + DecisionEngine creation
- CortexMemory init
- OutcomeTracker / EffectTracker / RecoveryTracker init
- RuleAdvisor init
- ChatSessionStore + RuleGenerator init
- `app.state.engine`, `app.state.rule_generator`, `app.state.chat_session_store`
- Rule advisor + suggestion cleanup background jobs
- Coordinator init
- Remove `ROOM_CONFIG_PATH` guard — MPC is always-on

**Simplify cortex router:** `create_cortex_router(sqlite, ws_server)` (no engine/advisor/memory/tracker params)

**Simplify `_handle_telemetry()`:** Keep only MPC path. Remove rules evaluate, outcome tracking, effect tracking, recovery tracking, baseline updates, context cache, LLM escalation.

**Delete methods:** `_build_context()`, `_update_baselines()`, `_handle_llm_result()`, `_execute_command()` (rules-only)

### 3B: `src/api/cortex.py` — Strip rules endpoints

**Remove endpoints:**
- `GET/POST/PUT/PATCH/DELETE /rules` (all CRUD)
- `GET/POST /adjustments` (suggestion approve/reject)
- `POST /advisor/run`
- `GET /baselines/{device_id}`
- `GET /conflicts`
- `GET /recoveries`

**Remove:** `_validate_rule()`, `_broadcast_rules()`, `AdjustmentAction`, `RuleToggle`, `RuleBody` models

**Simplify router factory:** `create_cortex_router(sqlite, ws_server=None)`

**Keep:** `/profiles` CRUD, `/profiles/{id}/goals` CRUD, `/health/{location}`, `/effects`, simplified `/status`

### 3C: `src/services/background_jobs.py` — Remove rules jobs

**Remove:** `start_rule_advisor_job()`, `start_suggestion_cleanup_job()`, related constants and imports

**Keep:** `start_aggregation_job()`, `start_command_expiration_job()`

### 3D: `src/services/intent_executor.py` — Remove rules intents

**Remove:** `generate_rules`, `approve_rules`, `refine_rules` intent handlers + `_format_proposed_rules()` helper

**Remove params:** `session_store`, `session_id`, `rule_generator`, `engine` from `execute_intent()` signature

**Keep:** `command`, `query`, `history`, `analyze`, `none` intents

### 3E: `src/api/chat.py` — Clean up execute_intent calls

**Remove:** `rule_generator` and `engine` from `getattr` lookups and `execute_intent()` calls (lines 34-35, 53, 84-85, 108)

**Keep:** `session_store` for multi-turn conversation context

### 3F: `src/services/websocket_server.py` — Remove rules broadcasts

**Remove:** `broadcast_rules()`, `broadcast_suggestions()` methods

### 3G: `src/config.py` — Remove rules config

**Remove:** `REJECTED_SUGGESTION_TTL_S`
**Keep:** `RULES_PATH` (still loads LLM config section), `ROOM_CONFIG_PATH`

**Verify:** `pytest tests/test_simulation_mpc.py tests/test_fast_physics.py tests/test_fast_physics_mpc.py tests/test_grow_profiles.py tests/test_derived_metrics.py tests/test_ecosystem_health.py -v`

---

## Phase 4: Refactor simulation framework (4 files)

### 4A: `simulations/runner.py` — Remove SimulationRunner + rules helpers

**Remove:**
- All rules imports (`DecisionEngine`, `_rules_from_dicts`, `OutcomeTracker`, `forecaster`, `analysis`, `rule_advisor`)
- `SimulationResult`, `AdaptiveResult`, `ControlComparisonResult` dataclasses
- `SimulationEvent`, `OutcomeScore`, `EffectObservation`, `EffectAggregator` dataclasses
- `SimulationRunner` class (entire ~600 lines)
- `run_adaptive()`, `run_control_comparison()`
- `_build_rule_performance_from_outcomes()`, `_avg_effectiveness()`, `_count_oscillations()`, `_compute_compliance()` (rules version), `DEFAULT_TARGET_RANGES`

**Keep:** `MPCSimulationResult`, `MultiDayResult`, `PhaseResult`, `run_mpc()`, `run_multi_day()`, `_compute_goal_compliance()`

### 4B: `simulations/simulate.py` — MPC-only CLI

**Remove CLI flags:** `--bang-bang`, `--hysteresis`, `--diurnal`, `--adaptive`, `--suboptimal`, `--compare-control`, `--compare-diurnal`, `--compare-mpc`, `--goal-aware`, `--learning-hours`

**Remove functions:** `_select_rules()`, `_get_goal_config()`, `_run_standard()`, `_run_adaptive()`, `_run_compare_control()`, `_run_compare_diurnal()`, `_run_compare_mpc()`, `_print_mpc_comparison()`

**Remove imports:** `plot_simulation`, `plot_adaptive`, `plot_control_comparison`, `plot_mpc_comparison`, `SimulationRunner`, `run_adaptive`, `run_control_comparison`, `RULES`, `BANG_BANG_RULES`, `SUBOPTIMAL_RULES`, `DIURNAL_RULES`

**Make MPC default:** `--mpc` flag becomes no-op. Default dispatch: `if args.multi_day → _run_multi_day() else → _run_mpc()`

**Keep:** `_build_mpc_config()`, `_run_mpc()`, `_run_multi_day()`, `_print_mpc_report()`, `--room`, `--variable`, `--fast-physics`, `--multi-day`, `--horizon`, `--w-energy`, `--duration`, `--start-hour`, `--strategy`, `-v`

### 4C: `simulations/charts.py` — Remove rules chart functions

**Remove:** `plot_simulation()`, `plot_adaptive()`, `plot_control_comparison()`, `plot_mpc_comparison()`, `_shade_actuator_periods()` (only used by removed functions)

**Remove imports:** `SimulationResult`, `AdaptiveResult`, `ControlComparisonResult`

**Keep:** `plot_mpc_simulation()`, `plot_multi_day()`, helpers they use (`_goal_range`, `_add_target_band`, `_add_checkpoints_and_phases`, `_build_phase_legend`, `ACTUATOR_STYLES`, `_MPC_ACTUATOR_COLORS`)

### 4D: `simulations/scenarios/default.py` — Remove rule sets

**Remove:** `RULES`, `BANG_BANG_RULES`, `SUBOPTIMAL_RULES`, `DIURNAL_RULES`, `DECLARED_EFFECTS`

**Keep:** `PROFILE`, `GOALS`, `FLOWER_GOALS`, `MPC_CONFIG`, `VARIABLE_OVERRIDES`

**Verify:** `pytest tests/test_simulation_mpc.py tests/test_fast_physics_mpc.py tests/test_simulation_multi_day.py tests/test_state_planner.py -v`

---

## Phase 5: Clean up SQLite + test_cortex_api (2 files)

### 5A: `src/services/sqlite_client.py` — Remove rules schema + CRUD

**Remove table creation:** `cortex_rules`, `cortex_conflicts`, `cortex_recoveries`

**Remove CRUD methods:** All rules methods (`insert_rule`, `update_rule`, `delete_rule`, `get_rule`, `get_all_rules`, `seed_rules_from_yaml`, etc.), suggestion methods, conflict methods, recovery methods, priority migration

**Keep:** `cortex_profiles`, `cortex_goals`, `cortex_health`, `cortex_effects`, `sensor_values`, `devices`, `commands`, `events`

### 5B: `tests/test_cortex_api.py` — Remove rules test cases

**Remove:** All rules CRUD tests, adjustments tests, advisor tests, baselines tests

**Simplify fixture:** `create_cortex_router(sqlite)` — no engine/advisor/memory/tracker

**Keep:** Profile/goals tests, health tests, status test (simplified)

**Verify:** `pytest tests/ -m "not e2e" -v` (full suite, expect all pass)

---

## Phase 6: Config + documentation cleanup

### 6A: `config/rules.yaml` — Keep LLM config only

Remove rules definitions, keep `llm:` section.

### 6B: Update `simulations/CLAUDE.md`

- Remove rules-based CLI examples (--bang-bang, --adaptive, etc.)
- Remove DecisionEngine from architecture diagram
- Update test command to remove deleted test files

### 6C: Update `apps/cortex/CLAUDE.md`

- Remove Phase 1-5 architecture (DecisionEngine, Outcome Tracking, Forecasting, Adaptive Learning, Coordination)
- Remove rules-related API endpoints
- Remove `{type: "rules"}` and `{type: "suggestions"}` from WebSocket section
- Remove deleted test files and service files from Key Files
- Add MPC architecture overview

### 6D: Update root `README.md`

- Remove rules engine references
- Update simulation CLI docs

**Final verify:** `pytest tests/ -m "not e2e" -v`

---

## Files Summary

| Action | Count | Description |
|--------|-------|-------------|
| **Delete tests** | 19 | Rules-only test files |
| **Delete services** | 11 | Rules-only service files |
| **Refactor production** | 7 | main.py, cortex.py, chat.py, background_jobs.py, intent_executor.py, websocket_server.py, config.py |
| **Refactor simulation** | 4 | runner.py, simulate.py, charts.py, default.py |
| **Refactor data layer** | 2 | sqlite_client.py, test_cortex_api.py |
| **Clean config/docs** | 4 | rules.yaml, 3x CLAUDE.md |
