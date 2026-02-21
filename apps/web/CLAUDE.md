# Web Frontend

React/Vite dashboard with Chart.js visualizations for IoT sensor monitoring and relay control.

## Build & Run

```bash
npm run dev    # Vite dev server on port 5173
npm run build  # Production build
```

## Environment Variables

- `VITE_API_PROXY_TARGET` — API proxy target (default: http://localhost:8000)

## Styling Stack

- Tailwind CSS v4 with `@import "tailwindcss"` syntax
- Custom theme variables in `@theme { }` block (e.g., `--color-panel`)
- Custom component classes in `@layer components { }` (glass-card, circle, tempCircle, etc.)
- CSS container queries (`[container-type:inline-size]`) for responsive gauges

## Routing (react-router-dom)

- `/` — Dashboard (device panels, drag-and-drop, sensor cards)
- `/nerve-center` — Nerve Center (rules, suggestions, baselines, system health)

## Layout Structure (App.tsx)

```
┌─────────────────────────────────────────────────────────────┐
│  Header: [Nerve Center btn] [Activity toggle]  Drawer(340px)│
├─────────────────────────────────────── ┌──────────────────┐ │
│  <Routes>                              │ Recent Activity   │ │
│    / → Dashboard (DevicePanels)        │ (slide-out right) │ │
│    /nerve-center → NerveCenter         │                  │ │
│      Tabs: Overview|Rules|Suggestions  └──────────────────┘ │
│            |Baselines                                        │
├─────────────────────────────────────────────────────────────┤
│  ChatInput (fixed bottom, backdrop-blur)                    │
└─────────────────────────────────────────────────────────────┘
```

## Drag-and-Drop

- Uses `@dnd-kit/core` + `@dnd-kit/sortable` for panel reordering
- `DragOverlay` renders dragged panel in a portal (preserves `backdrop-blur`)
- No CSS transforms on items — live array reorder via `onDragOver` instead
- Order persisted to SQLite `display_order` column via `PUT /api/devices/order`
- Grip handle in device header initiates drag; relay toggles and buttons unaffected

## Responsive Design

- Mobile: `px-3` padding, `min-w-[390px]` panels; Desktop: `px-5`
- Gauge circles use `clamp()` for fluid sizing: `--size: clamp(140px, 22cqw, 200px)`
- Charts use `h-[clamp(140px,20vh,200px)]` for fluid height
- Pinch zoom disabled via viewport meta (`user-scalable=no`)

## Chat & Voice (Frontend)

Uses `<detail>` tag pattern to separate display-only content from TTS-spoken content (see root CLAUDE.md for protocol):
- `stripDetail()` in ChatInput.tsx removes `<detail>...</detail>` blocks before passing to TTS
- `formatMessage()` strips the tag markers for display rendering
- `speakResponse()` calls `/api/voice/synthesize` for TTS

## API Dependency

Backend is Cortex (Python/FastAPI on port 8000). WebSocket at `/ws` provides real-time updates: `latest`, `relays`, `devices`, `commands`, `events`, `suggestions`, `rules`.

## Key Files

**Pages & App:**
- `src/App.tsx` — App shell with routing, shared state, header navigation
- `src/pages/Dashboard.tsx` — Main dashboard with device panels and drag-and-drop
- `src/pages/NerveCenter.tsx` — Nerve Center page (rules, suggestions, baselines, system health)

**Components:**
- `src/styles.css` — Global styles, Tailwind config, custom components
- `src/components/SensorCard.tsx` — Capabilities-driven sensor gauges and charts (special gauges for temp/humidity, generic readouts for others)
- `src/components/DevicePanel.tsx` — Per-device panel with drag-and-drop (via @dnd-kit)
- `src/components/ChatInput.tsx` — AI assistant input
- `src/components/ActivityCenter.tsx` — Activity feed (slide-out drawer)
- `src/components/NerveCenterOverview.tsx` — System health stats and advisor controls
- `src/components/NerveCenterRules.tsx` — Rule card grid with CRUD, category grouping, inline delete confirmation
- `src/components/RuleFormModal.tsx` — Centered modal for rule create/edit with collapsible advanced conditions
- `src/components/NerveCenterSuggestions.tsx` — Suggestion management with approve/reject
- `src/components/NerveCenterBaselines.tsx` — 24h baseline charts with ±1σ bands (Chart.js)
- `src/components/` — Additional: RelayControl, ObservationForm

**Hooks:**
- `src/hooks/useWebSocket.ts` — WebSocket connection with device/event/command/rules handlers
- `src/hooks/useRelays.ts` — Relay state management with offline detection
- `src/hooks/useOptimisticToggle.ts` — Toggle with ack timeout handling
- `src/hooks/useHistory.ts` — History fetching with deviceId filter

**API:**
- `src/api.ts` — REST + WebSocket client functions
