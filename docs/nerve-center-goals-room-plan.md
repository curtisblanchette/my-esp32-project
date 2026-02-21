# Nerve Center: Goals & Room Config UI

## Context

The MPC pivot removed the rules engine, but the Nerve Center frontend still references dead APIs (`/api/cortex/rules`, `/api/cortex/adjustments`, `/api/cortex/baselines`). The four existing tabs — Overview, Rules, Suggestions, Baselines — need to be replaced with MPC-relevant views: Goals management and Room Config display.

## New Tab Structure

| Tab | Description |
|-----|-------------|
| **Overview** | Health score, active profile summary, MPC status |
| **Goals** | Profile selector + goal CRUD with phase filtering |
| **Room** | Read-only room configuration display |

## Phase 1: Backend — Room Config Endpoint

**File:** `apps/cortex/src/api/cortex.py`

Add `GET /api/cortex/room-config` that reads the YAML file at `ROOM_CONFIG_PATH` and returns it as JSON. If `ROOM_CONFIG_PATH` is unset, return `{ok: true, configured: false, config: null}`. This avoids serializing Python callables — just return the raw YAML as JSON.

**File:** `apps/cortex/tests/test_cortex_api.py` — add test for the new endpoint.

## Phase 2: API Layer Cleanup (`apps/web/src/api.ts`)

**Remove dead types and functions:**
- Types: `CortexRule`, `RuleSuggestion`, `DeviceBaseline`, `ProposedRule`
- Functions: `fetchRules`, `toggleRule`, `createRule`, `updateRule`, `deleteRule`, `fetchAdjustments`, `fetchDeviceBaselines`, `runRuleAdvisor`, `resolveAdjustment`
- Update `CortexStatus` type to match simplified backend response
- Clean up `ChatResponse.action` type (remove `proposed_rules`, `rules_activated`)

**Add new types:**
- `GrowProfile` — `{id, location, name, strategy, phase, phaseStart, active, createdAt, updatedAt}`
- `GrowGoal` — `{id, profileId, metric, metricType, phase, rangeMin, rangeMax, tolerance, priority, schedule, timeWindow, createdAt, updatedAt}`
- `HealthSnapshot` — `{location, ts, score, detail}`
- `RoomConfig` — raw YAML structure

**Add new fetch functions:**
- `fetchProfiles()`, `createProfile()`, `updateProfile()`, `deleteProfile()`
- `fetchGoals(profileId, phase?)`, `createGoal(profileId, body)`, `updateGoal(id, body)`, `deleteGoal(id)`
- `fetchHealth(location, sinceMs?)`
- `fetchRoomConfig()`

## Phase 3: App.tsx Cleanup

**File:** `apps/web/src/App.tsx`

- Remove state: `suggestions`, `rules`, `setSuggestions`, `setRules`
- Remove handlers: `handleRunAdvisor`, `handleResolveAdjustment`
- Remove WS callbacks: `onSuggestionsUpdate`, `onRulesUpdate`
- Simplify NerveCenter props to `{devices, addError}`
- Remove suggestions from ActivityCenter props
- Remove dead imports: `resolveAdjustment`, `runRuleAdvisor`, `RuleSuggestion`, `CortexRule`

**File:** `apps/web/src/hooks/useWebSocket.ts` — remove `onSuggestionsUpdate`, `onRulesUpdate` callbacks.

**File:** `apps/web/src/components/ActivityCenter.tsx` — remove suggestions section and `RuleSuggestion` dependency.

## Phase 4: NerveCenter Page Rewrite

**File:** `apps/web/src/pages/NerveCenter.tsx`

- New tabs: Overview, Goals, Room
- Props simplified to `{devices, addError}`
- Fetch `profiles` on mount, pass to child components
- Remove all rules/suggestions references

## Phase 5: Overview Tab Rewrite

**File:** `apps/web/src/components/NerveCenterOverview.tsx`

Three stat cards:
1. **Health Score** — large number (0-100) with color coding, fetched from `fetchHealth(location)`
2. **Active Profile** — name, strategy badge, phase, days in phase
3. **Goal Count** — number of goals for active profile, link to Goals tab

## Phase 6: Goals Tab (new)

**File:** `apps/web/src/components/NerveCenterGoals.tsx`

- Profile selector bar at top (horizontal cards, "+ Create" button)
- Phase filter pills (All, seedling, veg, flower, late_flower, dry, cure)
- Goal cards: metric, range, tolerance, priority, type badge, edit/delete
- "+ Add Goal" button

**File:** `apps/web/src/components/ProfileFormModal.tsx`

- Fields: name, location, strategy (dropdown), phase (dropdown), active (toggle)
- Follows existing modal pattern from `RuleFormModal.tsx` (backdrop + ESC + click-outside)

**File:** `apps/web/src/components/GoalFormModal.tsx`

- Fields: metric (dropdown from device sensors + derived), metricType, phase, rangeMin/rangeMax, tolerance, priority, timeWindow
- Metric dropdown populated from `devices.capabilities.sensors`

## Phase 7: Room Tab (new)

**File:** `apps/web/src/components/NerveCenterRoom.tsx`

- Fetches room config on mount via `fetchRoomConfig()`
- Empty state if unconfigured: "No room configuration loaded"
- Sections in glass cards: Space, Actuators, Plant, Substrate (if present), Ventilation (if present), Initial Conditions

## Phase 8: Cleanup

- Delete: `NerveCenterRules.tsx`, `NerveCenterSuggestions.tsx`, `NerveCenterBaselines.tsx`, `RuleFormModal.tsx`
- Run `pytest tests/ -m "not e2e"` to verify backend
- Manual UI smoke test

## Styling Conventions

```
Strategy badges:  precision → red, balanced → blue, efficiency → green
Phase badges:     seedling → lime, veg → emerald, flower → purple, late_flower → amber, dry → stone, cure → cyan
MetricType:       sensor → blue, derived → purple, relay_schedule → amber
Health score:     >=80 → green, >=50 → yellow, <50 → red
Cards:            glass-card pattern (bg-panel/30, backdrop-blur-[6px], border-panel-border, rounded-xl)
Modals:           fixed inset-0 bg-black/60 backdrop-blur-sm, centered max-w-lg, ESC/click-outside close
```

## Key Files

| File | Action |
|------|--------|
| `apps/cortex/src/api/cortex.py` | Add room-config endpoint |
| `apps/cortex/tests/test_cortex_api.py` | Add room-config test |
| `apps/web/src/api.ts` | Remove dead code, add new types + functions |
| `apps/web/src/App.tsx` | Remove rules/suggestions state, simplify NerveCenter props |
| `apps/web/src/hooks/useWebSocket.ts` | Remove dead WS handlers |
| `apps/web/src/components/ActivityCenter.tsx` | Remove suggestions |
| `apps/web/src/pages/NerveCenter.tsx` | Rewrite tabs + data loading |
| `apps/web/src/components/NerveCenterOverview.tsx` | Rewrite with health/profile/MPC stats |
| `apps/web/src/components/NerveCenterGoals.tsx` | **NEW** — profiles + goals CRUD |
| `apps/web/src/components/ProfileFormModal.tsx` | **NEW** — profile create/edit modal |
| `apps/web/src/components/GoalFormModal.tsx` | **NEW** — goal create/edit modal |
| `apps/web/src/components/NerveCenterRoom.tsx` | **NEW** — room config display |
| `apps/web/src/components/NerveCenterRules.tsx` | **DELETE** |
| `apps/web/src/components/NerveCenterSuggestions.tsx` | **DELETE** |
| `apps/web/src/components/NerveCenterBaselines.tsx` | **DELETE** |
| `apps/web/src/components/RuleFormModal.tsx` | **DELETE** |

## Verification

1. `pytest tests/ -m "not e2e"` — backend tests pass
2. `npm run build` in `apps/web/` — no TypeScript errors
3. Manual: navigate to Nerve Center, verify 3 tabs render
4. Manual: create a profile, add goals, verify CRUD works
5. Manual: Room tab shows config (or empty state)