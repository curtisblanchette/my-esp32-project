# Multi-Device Dashboard with Discovery State

## Summary
Transform the dashboard from single-device to multi-device architecture:
1. Show discovery loading state on startup while waiting for device birth messages
2. Render separate panels for each registered device
3. Dynamically show gauges only if device has temp/humidity sensors
4. Dynamically show relays only if device has actuators
5. Store telemetry per-device so each panel shows its own readings

---

## Phase 1: Backend - Per-Device Telemetry Storage

### Files to Modify

| File | Changes |
|------|---------|
| `apps/api/src/lib/sqlite.ts` | Add `device_id` column to sensor_readings, update queries |
| `apps/api/src/services/mqttTelemetry.ts` | Include `deviceId` when storing readings |
| `apps/api/src/services/websocket.ts` | Include `deviceId` in latest reading broadcasts |
| `apps/api/src/routes/telemetry.ts` | Add device filter to history endpoint |

### Implementation

**1.1 Update sensor_readings schema** (`sqlite.ts`)
```sql
-- Add device_id column (migration needed for existing data)
ALTER TABLE sensor_readings ADD COLUMN device_id TEXT;
CREATE INDEX idx_readings_device ON sensor_readings(device_id, ts);
```

**1.2 Update storeReading()** (`sqlite.ts`)
```typescript
export function storeReading(r: {
  ts: number;
  temp: number;
  humidity: number;
  deviceId: string;  // NEW
  sourceTopic: string
}): void
```

**1.3 Update handleTelemetry()** (`mqttTelemetry.ts`)
- Pass `deviceId` from MQTT topic to `storeReading()`
- Update `setLatest()` to store per-device: `Map<deviceId, LatestReading>`

**1.4 Update WebSocket broadcasts** (`websocket.ts`)
- Change `{type: "latest", data: reading}` to include deviceId
- Or broadcast `{type: "latest", data: Map<deviceId, reading>}`

**1.5 Update history endpoint** (`telemetry.ts`)
- Add optional `?deviceId=` query param to `/api/history`

---

## Phase 2: Frontend - Multi-Device Architecture

### Files to Modify

| File | Changes |
|------|---------|
| `apps/web/src/App.tsx` | Track devices array, render DevicePanel per device |
| `apps/web/src/api.ts` | Update LatestReading type to include deviceId |
| `apps/web/src/components/DevicePanel.tsx` | **NEW** - Extracted device panel component |
| `apps/web/src/components/DeviceDiscoveryState.tsx` | **NEW** - Discovery loading component |
| `apps/web/src/hooks/useWebSocket.ts` | Already supports onDevicesUpdate (no changes) |
| `apps/web/src/styles.css` | Add radar-ping animation |

### Implementation

**2.1 Create DevicePanel component**
Extract current device content from App.tsx into reusable component:
```typescript
type DevicePanelProps = {
  device: Device;
  latestReading: LatestReading | null;
  relays: RelayStatus[];
  onRelayStateChange: (id: string, state: boolean) => void;
  onRelayNameChange: (id: string, name: string) => void;
  onError: (message: string, source?: string) => void;
};
```

Conditionally render based on capabilities:
```tsx
{/* Only show gauges if device has temp/humidity sensors */}
{hasTempHumiditySensors(device) && (
  <SensorCard ... />
)}

{/* Only show relays if device has actuators */}
{device.capabilities.actuators.length > 0 && (
  <RelayControls relays={deviceRelays} ... />
)}
```

**2.2 Create DeviceDiscoveryState component**
```tsx
<div className="flex-1 md:flex-[3] min-w-0 border border-panel-border rounded-2xl p-5 backdrop-blur-[10px] flex items-center justify-center min-h-[400px]">
  <div className="text-center">
    {/* Animated radar icon */}
    <div className="relative inline-flex items-center justify-center w-20 h-20 mb-4">
      <div className="absolute inset-0 rounded-full bg-blue-500/10 animate-ping" />
      <div className="absolute inset-3 rounded-full bg-blue-500/15 animate-ping [animation-delay:0.5s]" />
      <div className="relative z-10 w-12 h-12 rounded-full bg-blue-500/20 border border-blue-500/30 flex items-center justify-center">
        <WifiIcon className="w-6 h-6 text-blue-400" />
      </div>
    </div>
    <div className="text-sm font-medium text-blue-400">Discovering devices on the network</div>
    <div className="text-xs opacity-60 mt-1">Waiting for device registration...</div>
  </div>
</div>
```

**2.3 Update App.tsx state management**
```typescript
// New state
const [devices, setDevices] = useState<Device[]>([]);
const [discoveryPhase, setDiscoveryPhase] = useState<"discovering" | "complete">("discovering");
const [latestByDevice, setLatestByDevice] = useState<Record<string, LatestReading>>({});

// Wire up onDevicesUpdate
const { isConnected } = useWebSocket({
  onDevicesUpdate: (deviceList) => {
    setDevices(deviceList);
    setDiscoveryPhase("complete");
  },
  onLatestReading: (reading) => {
    // reading now includes deviceId
    setLatestByDevice(prev => ({
      ...prev,
      [reading.deviceId]: reading
    }));
  },
  // ... other callbacks
});
```

**2.4 Render device panels**
```tsx
{/* Main content */}
<div className="flex-1 w-screen flex justify-center p-5 pb-40">
  <div className="w-full max-w-[1400px] flex flex-col gap-5">

    {/* Device panels */}
    <div className="flex flex-wrap gap-5">
      {discoveryPhase === "discovering" && devices.length === 0 ? (
        <DeviceDiscoveryState />
      ) : devices.length > 0 ? (
        devices.map(device => (
          <DevicePanel
            key={device.id}
            device={device}
            latestReading={latestByDevice[device.id] || null}
            relays={relays.filter(r => r.deviceId === device.id)}
            onRelayStateChange={handleStateChange}
            onRelayNameChange={handleNameChange}
            onError={addError}
          />
        ))
      ) : (
        <div className="text-sm opacity-60">No devices found on the network.</div>
      )}
    </div>

    {/* Recent Activity sidebar - moves below on mobile */}
    <RecentActivity ... />
  </div>
</div>
```

---

## Phase 3: Capability Detection Helpers

**Add to api.ts or utils:**
```typescript
export function hasSensor(device: Device, type: string): boolean {
  return device.capabilities.sensors.some(s => s.type === type);
}

export function hasTempHumiditySensors(device: Device): boolean {
  return hasSensor(device, "temperature") && hasSensor(device, "humidity");
}

export function hasActuators(device: Device): boolean {
  return device.capabilities.actuators.length > 0;
}
```

---

## Data Flow

```
ESP32 Birth Message
       ↓
MQTT: home/_registry/{deviceId}/birth
       ↓
mqttTelemetry.ts: handleBirth() → upsertDevice()
       ↓
websocket.ts: broadcastDevices() → {type: "devices", data: Device[]}
       ↓
App.tsx: onDevicesUpdate → setDevices(), setDiscoveryPhase("complete")
       ↓
Render: devices.map(d => <DevicePanel device={d} ... />)
```

```
ESP32 Telemetry
       ↓
MQTT: home/{location}/{deviceId}/telemetry
       ↓
mqttTelemetry.ts: handleTelemetry() → storeReading({deviceId, ...})
       ↓
websocket.ts: broadcastLatest() → {type: "latest", data: {deviceId, ...}}
       ↓
App.tsx: onLatestReading → setLatestByDevice({[deviceId]: reading})
       ↓
Render: <DevicePanel latestReading={latestByDevice[device.id]} />
```

---

## Files Summary

| File | Type | Purpose |
|------|------|---------|
| `apps/api/src/lib/sqlite.ts` | Modify | Add device_id to readings schema |
| `apps/api/src/services/mqttTelemetry.ts` | Modify | Pass deviceId through telemetry flow |
| `apps/api/src/services/websocket.ts` | Modify | Include deviceId in latest broadcasts |
| `apps/api/src/routes/telemetry.ts` | Modify | Add deviceId filter to history |
| `apps/web/src/App.tsx` | Modify | Multi-device state, render DevicePanels |
| `apps/web/src/api.ts` | Modify | Update LatestReading type |
| `apps/web/src/components/DevicePanel.tsx` | **NEW** | Extracted device panel |
| `apps/web/src/components/DeviceDiscoveryState.tsx` | **NEW** | Discovery loading UI |
| `apps/web/src/styles.css` | Modify | Add radar-ping animation |

---

## Verification

1. **Backend**:
   - Start services: `docker compose up -d`
   - Check schema migration applied
   - Verify `/api/history?deviceId=esp32-1` filters correctly

2. **Discovery State**:
   - Stop all ESP32 devices
   - Open dashboard → should show discovery animation

3. **Device Registration**:
   - Power on ESP32 → panel appears with device name/location
   - Panel shows gauges only if device has temp/humidity sensors
   - Panel shows relays only if device has actuators

4. **Per-Device Telemetry**:
   - With 2 devices, each panel shows its own temp/humidity
   - History charts are device-specific

5. **Offline Handling**:
   - Device goes offline → panel shows offline indicator
   - Device comes back → panel updates to online

---

## Status

**Completed**: All phases implemented.
