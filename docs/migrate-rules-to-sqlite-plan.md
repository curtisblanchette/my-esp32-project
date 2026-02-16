# Migrate Rules to SQLite + CRUD UI

## Context
Rules live in `rules.yaml` and all runtime state (enable/disable, advisor threshold changes) is in-memory only — lost on restart. This creates drift risk between YAML and runtime state.

**Solution:** SQLite becomes the single source of truth for rules. YAML serves only as a seed file for first run. A full CRUD UI lets users create, edit, and delete rules from the Nerve Center.

## Design Principles
- `cortex_rules` SQLite table is the sole source of truth
- `rules.yaml` seeds the DB on first run (empty table), then is never read again for rules
- LLM config stays in `rules.yaml` (not a rule)
- Condition/action stored as JSON columns (matches existing patterns: `devices.capabilities`, `commands.value`)
- `id` (UUID) is PRIMARY KEY — stable, never changes. `name` is UNIQUE but mutable (user can rename)
- `cortex_suggestions` keeps `rule_name` as-is — suggestions are transient, no FK migration needed
- Cascade delete on rule deletion matches by name (`DELETE FROM cortex_suggestions WHERE rule_name = ?`)
- `has_duplicate_suggestion()` stays unchanged (matches on rule_name + field + value)
- `modified` computed from `updated_at > created_at` — no separate tracking set
- API routes use `id` for PATCH/PUT/DELETE, not name

## Ripple Effects of adding `id` to rules
- `DecisionEngine`: Rule dataclass gets `id: str` field, `modified_rules: set[str]` removed (DB computes `modified`)
- `RuleAdvisor._apply_to_engine()`: persists changes to DB by rule `id`
- `_cross:{sensor}:{rule_name}` state keys in decision_engine — keep as-is (internal state, uses name for readability)
- Frontend `CortexRule` type — add `id: string`, keep `name` as display/editable field
- Frontend toggle/edit/delete calls use `id`, not `name`

## Files to Modify

| File | Change |
|------|--------|
| `apps/cortex/src/services/sqlite_client.py` | `cortex_rules` table, CRUD methods, seed method |
| `apps/cortex/src/services/decision_engine.py` | Add `id` to Rule dataclass, `from_sqlite()`, `reload_from_sqlite()` |
| `apps/cortex/src/services/rule_advisor.py` | `_apply_to_engine()` persists to DB by rule `id` |
| `apps/cortex/src/api/cortex.py` | POST/PUT/DELETE endpoints by `id`, validation, toggle persists |
| `apps/cortex/src/main.py` | Startup: seed → load from SQLite → LLM config from YAML |
| `apps/web/src/api.ts` | `createRule()`, `updateRule()`, `deleteRule()` + type updates |
| `apps/web/src/components/NerveCenterRules.tsx` | Create/edit/delete buttons, modal integration |
| `apps/web/src/components/RuleFormModal.tsx` | **New** — centered modal form for create/edit |
| `apps/cortex/tests/test_rules_crud.py` | **New** — CRUD, seed, API, persistence tests |

---

## Plan

### 1. SQLite — `cortex_rules` table + CRUD
**File**: `apps/cortex/src/services/sqlite_client.py`

Add table to `connect()` alongside existing tables:
```sql
CREATE TABLE IF NOT EXISTS cortex_rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    condition JSON NOT NULL,
    action JSON NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'yaml',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
```

Methods:
- `insert_rule(name, description, condition, action, enabled, source)` → dict (generates UUID)
- `update_rule(id, name?, description?, condition?, action?, enabled?)` → dict | None
- `delete_rule(id)` → bool — **cascade deletes** suggestions with matching `rule_name`
- `get_rule(id)` → dict | None
- `get_rule_by_name(name)` → dict | None
- `get_all_rules()` → list[dict]
- `update_rule_enabled(id, enabled)` → bool
- `update_rule_condition_field(id, field, value)` → bool (for RuleAdvisor)
- `count_rules()` → int
- `seed_rules_from_yaml(yaml_path)` → int (imports if table empty, returns 0 otherwise)
- `_rule_row_to_dict(row)` → dict (camelCase, computes `modified: updated_at > created_at`)

### 2. DecisionEngine — load from SQLite
**File**: `apps/cortex/src/services/decision_engine.py`

- Add `id: str` field to `Rule` dataclass (default empty string for backward compat with tests using `from_yaml`)
- `from_sqlite(sqlite_client)` classmethod — loads rules from DB into dataclasses with `id`
- `reload_from_sqlite(sqlite_client)` — replaces `self.rules` from DB, preserves `sensor_states` and `llm_config`
- Remove `modified_rules: set[str]` — no longer needed, DB computes `modified`
- Keep `from_yaml()` for backward compat (tests, LLM config loading)

### 3. RuleAdvisor — persist changes
**File**: `apps/cortex/src/services/rule_advisor.py`

- Update `_apply_to_engine()` to also call `sqlite.update_rule_condition_field(rule.id, field, value)` after in-memory `setattr()`
- Suggestions continue to use `rule_name` — no changes to suggestion creation or `has_duplicate_suggestion()`

### 4. API — CRUD endpoints + validation
**File**: `apps/cortex/src/api/cortex.py`

**New endpoints:**
- `POST /api/cortex/rules` — create rule (validate, insert, reload engine, broadcast)
- `PUT /api/cortex/rules/{id}` — update rule (validate, update, reload engine, broadcast)
- `DELETE /api/cortex/rules/{id}` — delete rule (cascade delete suggestions, reload engine, broadcast)

**Modified:**
- `PATCH /api/cortex/rules/{id}` — toggle now persists via `update_rule_enabled(id)`
- `GET /api/cortex/rules` — serialize from DB via `get_all_rules()`

**Validation helper** `_validate_rule(condition, action)`:
- Required: `sensor`, `operator` (one of `> < >= <= == !=`), `threshold`, `target`, `action`, `reason`
- Conditional: `forecast_threshold` required when `forecast` is set
- Optional fields validated when present: `trend` ∈ {rising, falling, stable}, `forecast` ∈ {will_exceed, will_drop_below}

### 5. Startup flow
**File**: `apps/cortex/src/main.py`

Move engine init to after `_wait_for_api()` (needs sqlite):
1. `sqlite.seed_rules_from_yaml(rules_path)` — seed on first run
2. `self.engine = DecisionEngine.from_sqlite(sqlite)` — load from DB
3. Load LLM config from YAML separately: `yaml.safe_load()` → `engine.llm_config`

### 6. Frontend API functions
**File**: `apps/web/src/api.ts`

- Extend `CortexRule` type: add `id: string`, `source: "yaml" | "user"`, `createdAt: number`, `updatedAt: number`
- `createRule(rule)` → POST
- `updateRule(id, updates)` → PUT
- `deleteRule(id)` → DELETE
- `toggleRule(id, enabled)` → PATCH (update existing to use `id` instead of `name`)

### 7. UI — RuleFormModal (centered modal)
**File**: `apps/web/src/components/RuleFormModal.tsx` (new)

Centered modal with backdrop overlay — more space for the form fields than a narrow drawer.

**Layout:**
- Backdrop: semi-transparent dark overlay, click-outside to close
- Modal: `max-w-2xl` centered, glass-card styling, max-height with scroll
- Header: title ("Create Rule" / "Edit Rule") + close button
- Footer: Cancel + Submit buttons

**Form sections:**
1. **Basic**: name (editable in both modes), description, enabled toggle
2. **Condition**: sensor (select), operator (select), threshold (number), duration_seconds (number)
3. **Advanced** (collapsible): trend, time_of_day, forecast, forecast_threshold, baseline_deviation, scope
4. **Action**: target, action, value, reason, target_scope

Create mode: fields empty/defaults
Edit mode: pre-populated from rule data
Submit: calls `createRule`/`updateRule`, WS broadcast updates the list

### 8. UI — NerveCenterRules integration
**File**: `apps/web/src/components/NerveCenterRules.tsx`

- "+ Create Rule" button next to filter bar
- Edit (pencil) and Delete (trash) icon buttons on each card
- Delete uses inline confirmation: click → "Confirm?" → click again to delete
- State: `modalOpen`, `editingRule`, `deletingRule`
- All API calls use `rule.id` not `rule.name`

### 9. Tests
**File**: `apps/cortex/tests/test_rules_crud.py` (new)

**SQLite CRUD:**
- insert + get round-trip, duplicate name raises (UNIQUE), rename via update, delete by id, count, modified flag, seed from YAML (empty/non-empty/missing file)
- delete cascade: deleting a rule also deletes its associated suggestions

**API endpoints:**
- GET returns DB rules with `id`, POST create (success/duplicate 409/invalid 400), PUT update by id (success/rename/404), DELETE by id (success/404), PATCH toggle persists by id

**Engine loading:**
- `from_sqlite` loads all fields including `id`, empty DB → empty rules, reload preserves sensor_states

**Advisor persistence:**
- `_apply_to_engine` persists to DB by rule id, auto-apply writes through

---

## Verification
1. `pytest tests/ -m "not e2e"` — all tests pass
2. `npx tsc --noEmit` — frontend type-checks
3. Start Cortex fresh (empty DB) → rules seeded from YAML → visible in UI
4. Disable a rule → restart → stays disabled
5. Create a rule in UI → appears in list → survives restart
6. Edit a rule (including rename) → changes persist → engine uses new values
7. Delete a rule → gone from list, DB, and suggestions cascade-deleted
8. Run advisor → auto-applied threshold changes persist across restart
