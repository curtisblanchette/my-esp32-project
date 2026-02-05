# Plan: Move Device Registry to Redis

## Goal
Eliminate SQLite queries from hot paths (like `buildSystemPrompt()`) by adding an in-memory device registry backed by Redis.

## Architecture

```
Hot Path (reads)         In-Memory Map (deviceRegistry.ts)
                              ^           |
                              |           | (sync on write)
MQTT birth/will ------------->|           v
                         Redis (durable cache)
                              ^           |
                              |           | (cold start bootstrap)
                         SQLite (source of truth)
```

## Implementation

### 1. Create `apps/api/src/state/deviceRegistry.ts` (NEW)

In-memory Map following the `latestReading.ts` pattern:

```typescript
const devicesByID = new Map<string, Device>();

// Reads (hot path - in-memory only)
export function getAllDevices(): Device[]
export function getDevice(deviceId: string): Device | null
export function getOnlineDevices(): Device[]
export function getDeviceActuators(deviceId?: string): DeviceActuator[]

// Writes (update map, caller syncs to Redis/SQLite)
export function setDevice(device: Device): void
export function setDeviceOnline(deviceId: string): boolean
export function setDeviceOffline(deviceId: string): boolean

// Bootstrap
export function loadDevices(devices: Device[]): void
```

### 2. Extend `apps/api/src/lib/redis.ts`

Add device storage functions:

```typescript
// Keys: device:{deviceId}, devices:index (Set of IDs)
export async function storeDevice(device: Device): Promise<void>
export async function getDeviceFromRedis(deviceId: string): Promise<Device | null>
export async function getAllDevicesFromRedis(): Promise<Device[]>
export async function updateDeviceOnlineStatus(deviceId: string, online: boolean): Promise<void>
```

### 3. Update `apps/api/src/server.ts`

Add bootstrap on startup:
1. Try Redis first (warm start)
2. Fall back to SQLite (cold start)
3. Sync SQLite devices to Redis if cold start

### 4. Update `apps/api/src/services/mqttTelemetry.ts`

Modify `handleBirth()` and `handleWill()` to write to all three layers:
1. In-memory (immediate)
2. Redis (async)
3. SQLite (async)

### 5. Update imports in consumers

| File | Change |
|------|--------|
| `services/systemPrompt.ts` | Import `getAllDevices` from `deviceRegistry` instead of `sqlite` |
| `services/websocket.ts` | Import device functions from `deviceRegistry` |
| `routes/devices.ts` | Import from `deviceRegistry` |
| `routes/relays.ts` | Import `getDeviceActuators` from `deviceRegistry` |

## Files to Modify

- `apps/api/src/state/deviceRegistry.ts` - **NEW**
- `apps/api/src/lib/redis.ts` - Add device storage functions
- `apps/api/src/server.ts` - Add bootstrap initialization
- `apps/api/src/services/mqttTelemetry.ts` - Write to all layers on birth/will
- `apps/api/src/services/systemPrompt.ts` - Change import
- `apps/api/src/services/websocket.ts` - Change import
- `apps/api/src/routes/devices.ts` - Change import
- `apps/api/src/routes/relays.ts` - Change import

## Out of Scope

- Relay config migration (can follow same pattern later if needed)

## Verification

1. Start server, verify devices load from SQLite on cold start
2. Restart server, verify devices load from Redis (faster)
3. Send device birth message, verify in-memory + Redis + SQLite all updated
4. Call `/api/devices` and confirm fast response
5. Use chat to trigger `buildSystemPrompt()`, verify no SQLite query logged