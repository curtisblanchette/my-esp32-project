# Debugging: WebSocket Streaming for Events and Commands

## Goal
Replace 10-second polling with real-time WebSocket streaming, and ensure relay toggle switches only update when the device ack is received (synchronized with the event appearing in recent activity).

## What Was Implemented

### Backend Changes

1. **`apps/api/src/services/websocket.ts`**
   - Added `broadcastEvent()` - broadcasts individual events to all WebSocket clients
   - Added `broadcastCommand()` - broadcasts individual commands to all WebSocket clients
   - Sends initial events and commands on WebSocket connection

2. **`apps/api/src/services/mqttTelemetry.ts`**
   - Imports `broadcastEvent` and `broadcastCommand`
   - Calls `broadcastEvent()` after every `insertEvent()` (in handleAck, handleBirth, handleWill)
   - Calls `broadcastCommand()` after `insertCommand()` in handleCommand
   - **Important**: In `handleAck`, event is broadcast BEFORE `broadcastDevices()` so the frontend receives events in the right order

3. **`apps/api/src/routes/relays.ts`**
   - Added `broadcastCommand()` call after inserting command from dashboard toggle

### Frontend Changes

1. **`apps/web/src/hooks/useWebSocket.ts`**
   - Added message types: `events`, `event`, `commands`, `command`
   - Added callbacks: `onEventsUpdate`, `onEventReceived`, `onCommandsUpdate`, `onCommandReceived`

2. **`apps/web/src/App.tsx`**
   - Wired up all new WebSocket callbacks
   - **Key sync logic**: On `command_ack` event, syncs relay state from `event.data.target` and `event.data.actualValue`
   - Removed polling - no more `fetchCommands` or `fetchEvents` calls

3. **`apps/web/src/hooks/useOptimisticToggle.ts`**
   - Changed from optimistic to **pessimistic** updates
   - Toggle stays in loading state until WebSocket confirms state change
   - Uses 10-second timeout for ack waiting

## Current Issue

**Symptom**: Manual toggle acks aren't being received/processed by the frontend. The toggle doesn't sync with the event appearing in recent activity.

**Evidence from logs**:
```
[MQTT] Published command to home/room1/esp32-1/command: {...}
[MQTT] Command cmd-7280c2c9 already exists, skipping
[MQTT] Ack received for cmd-7280c2c9: executed
```

The backend IS receiving the ack from the device. The question is:
1. Does the ack payload contain `target` and `actualValue`?
2. Is `broadcastEvent()` being called?
3. Is the event reaching the frontend WebSocket?
4. Is the frontend correctly parsing the event data?

## Next Debugging Steps

### 1. Verify ack payload fields
Added logging in `handleAck`:
```typescript
console.log(`[MQTT] Ack received for ${p.correlationId}: ${p.status}`, { target: p.target, actualValue: p.actualValue });
```

Check if `target` and `actualValue` are present and valid.

### 2. Verify device sends correct ack
Check `device/lib/home_hub.py` `publish_ack()` method:
```python
def publish_ack(self, correlation_id: str, status: str, target: str, actual_value, error: str = None):
    payload = {
        "correlationId": correlation_id,
        "status": status,
        "target": target,
        "actualValue": actual_value
    }
```

### 3. Add frontend logging
In `App.tsx` `onEventReceived` callback:
```typescript
onEventReceived: (event) => {
  console.log('[WS] Event received:', event);
  // ...
}
```

### 4. Check broadcast is called
Add logging in `handleAck` before `broadcastEvent()`:
```typescript
console.log(`[MQTT] Broadcasting command_ack event:`, { id: event.id, payload: event.payload });
broadcastEvent(event);
```

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           COMMAND FLOW                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Dashboard Toggle Click                                                      │
│         │                                                                    │
│         ▼                                                                    │
│  POST /api/relays/:id ──► insertCommand() ──► broadcastCommand()            │
│         │                        │                    │                      │
│         ▼                        ▼                    ▼                      │
│  publishCommand() ───► MQTT Topic ─────────► WebSocket "command"            │
│         │              (device receives)        (frontend shows             │
│         │                     │                  in activity)               │
│         │                     ▼                                             │
│         │              Device executes                                       │
│         │                     │                                             │
│         │                     ▼                                             │
│         │              Device publishes ack                                  │
│         │                     │                                             │
│         │                     ▼                                             │
│         │              MQTT ack topic ──► handleAck()                       │
│         │                                      │                            │
│         │                                      ├──► insertEvent()           │
│         │                                      │         │                  │
│         │                                      │         ▼                  │
│         │                                      │    broadcastEvent()        │
│         │                                      │         │                  │
│         │                                      │         ▼                  │
│         │                                      │    WebSocket "event"       │
│         │                                      │         │                  │
│         │                                      │         ▼                  │
│         │                                      │    App.tsx onEventReceived │
│         │                                      │         │                  │
│         │                                      │         ├──► setEvents()   │
│         │                                      │         │    (activity)    │
│         │                                      │         │                  │
│         │                                      │         └──► handleStateChange()
│         │                                      │              (relay toggle) │
│         │                                      │                            │
│         │                                      ├──► updateRelayConfig()     │
│         │                                      │                            │
│         │                                      └──► broadcastDevices()      │
│         │                                                │                  │
│         │                                                ▼                  │
│         │                                          WebSocket "relays"       │
│         │                                                │                  │
│         │                                                ▼                  │
│         │                                          applyRelays()            │
│         │                                          (useRelays hook)         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Files

| File | Purpose |
|------|---------|
| `apps/api/src/services/mqttTelemetry.ts:246-306` | handleAck - processes device acks |
| `apps/api/src/services/websocket.ts:185-200` | broadcastEvent function |
| `apps/web/src/App.tsx:64-72` | onEventReceived callback |
| `apps/web/src/hooks/useRelays.ts` | handleStateChange function |
| `device/lib/home_hub.py` | publish_ack method |

## Status

**Working**:
- Commands streaming via WebSocket
- Events streaming via WebSocket
- Initial data load via WebSocket
- Polling removed

**Not Working**:
- Toggle doesn't sync with command_ack event appearance in activity
- Need to verify ack payload contains required fields