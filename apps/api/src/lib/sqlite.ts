import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

import { config } from "../config/index.js";

export type TelemetryRow = {
  ts: number;
  temp: number;
  humidity: number;
  sourceTopic: string | null;
  deviceId: string | null;
};

export type TelemetryBucketRow = {
  ts: number;
  temp: number;
  humidity: number;
  count: number;
  deviceId: string | null;
};

export type Event = {
  id: number;
  ts: number;
  deviceId: string;
  eventType: string;
  payload: unknown;
  source: string | null;
};

export type Command = {
  id: string; // correlationId
  ts: number;
  deviceId: string;
  target: string;
  action: string;
  value: unknown;
  source: string;
  reason: string | null;
  ttl: number; // milliseconds until command expires
  status: "pending" | "acked" | "failed" | "expired";
  ackTs: number | null;
  ackPayload: unknown;
};

export type Actuator = {
  id: string;
  type: string;
  pin?: number;
  name?: string;
  state?: boolean;
};

export type Sensor = {
  id: string;
  type: string;
  name?: string;
};

export type DeviceCapabilities = {
  sensors: Sensor[];
  actuators: Actuator[];
};

export type Device = {
  id: string;
  location: string;
  name: string | null;
  platform: string | null;
  firmware: string | null;
  capabilities: DeviceCapabilities;
  actuatorNames: Record<string, string>;
  telemetryIntervalMs: number | null;
  online: boolean;
  lastSeen: number;
  createdAt: number;
  updatedAt: number;
};

let db: DatabaseSync | null = null;
let didLogDbPath = false;

export function getDb(): DatabaseSync {
  if (db) return db;

  const dbPath = path.resolve(config.sqlitePath);
  if (!didLogDbPath) {
    didLogDbPath = true;
    console.log(`SQLite DB path: ${dbPath}`);
    console.log(`SQLite journal_mode: ${config.sqliteJournalMode}`);
  }
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });

  db = new DatabaseSync(dbPath);
  db.exec(`
    PRAGMA journal_mode = ${config.sqliteJournalMode};
    PRAGMA synchronous = NORMAL;

    CREATE TABLE IF NOT EXISTS sensor_readings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts INTEGER NOT NULL,
      temp REAL NOT NULL,
      humidity REAL NOT NULL,
      source_topic TEXT,
      device_id TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_sensor_readings_ts ON sensor_readings(ts);

    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts INTEGER NOT NULL,
      device_id TEXT NOT NULL,
      event_type TEXT NOT NULL,
      payload JSON,
      source TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
    CREATE INDEX IF NOT EXISTS idx_events_device ON events(device_id);

    CREATE TABLE IF NOT EXISTS commands (
      id TEXT PRIMARY KEY,
      ts INTEGER NOT NULL,
      device_id TEXT NOT NULL,
      target TEXT NOT NULL,
      action TEXT NOT NULL,
      value JSON,
      source TEXT NOT NULL,
      reason TEXT,
      ttl INTEGER NOT NULL DEFAULT 30000,
      status TEXT NOT NULL DEFAULT 'pending',
      ack_ts INTEGER,
      ack_payload JSON
    );
    CREATE INDEX IF NOT EXISTS idx_commands_ts ON commands(ts);
    CREATE INDEX IF NOT EXISTS idx_commands_device ON commands(device_id);
    CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status);

    CREATE TABLE IF NOT EXISTS devices (
      id TEXT PRIMARY KEY,
      location TEXT NOT NULL,
      name TEXT,
      platform TEXT,
      firmware TEXT,
      capabilities JSON NOT NULL,
      actuator_names JSON NOT NULL DEFAULT '{}',
      telemetry_interval_ms INTEGER,
      online INTEGER NOT NULL DEFAULT 1,
      last_seen INTEGER NOT NULL,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_devices_location ON devices(location);
    CREATE INDEX IF NOT EXISTS idx_devices_online ON devices(online);
  `);

  // Migrations for existing databases
  runMigrations(db);

  return db;
}

function runMigrations(db: DatabaseSync): void {
  // Add ttl column to commands table if it doesn't exist
  const commandsColumnsStmt = db.prepare("PRAGMA table_info(commands)");
  const commandsColumns = commandsColumnsStmt.all() as Array<{ name: string }>;
  const hasttl = commandsColumns.some((col) => col.name === "ttl");

  if (!hasttl) {
    console.log("Migration: Adding ttl column to commands table");
    db.exec("ALTER TABLE commands ADD COLUMN ttl INTEGER NOT NULL DEFAULT 30000");
  }

  // Add device_id column to sensor_readings table if it doesn't exist
  const readingsColumnsStmt = db.prepare("PRAGMA table_info(sensor_readings)");
  const readingsColumns = readingsColumnsStmt.all() as Array<{ name: string }>;
  const hasDeviceId = readingsColumns.some((col) => col.name === "device_id");

  if (!hasDeviceId) {
    console.log("Migration: Adding device_id column to sensor_readings table");
    db.exec("ALTER TABLE sensor_readings ADD COLUMN device_id TEXT");
  }

  // Always ensure the device_id index exists (for both migrated and new tables)
  db.exec("CREATE INDEX IF NOT EXISTS idx_sensor_readings_device ON sensor_readings(device_id, ts)");

  // Add actuator_names column to devices table if it doesn't exist
  const devicesColumnsStmt = db.prepare("PRAGMA table_info(devices)");
  const devicesColumns = devicesColumnsStmt.all() as Array<{ name: string }>;
  const hasActuatorNames = devicesColumns.some((col) => col.name === "actuator_names");

  if (!hasActuatorNames) {
    console.log("Migration: Adding actuator_names column to devices table");
    db.exec("ALTER TABLE devices ADD COLUMN actuator_names JSON NOT NULL DEFAULT '{}'");
  }

  // Drop relay_config table if it exists (no longer needed)
  db.exec("DROP TABLE IF EXISTS relay_config");
}

export function insertReading(row: TelemetryRow): void {
  const d = getDb();
  const stmt = d.prepare(
    "INSERT INTO sensor_readings (ts, temp, humidity, source_topic, device_id) VALUES (?, ?, ?, ?, ?)"
  );
  stmt.run(row.ts, row.temp, row.humidity, row.sourceTopic, row.deviceId ?? null);
}

export function queryHistoryRaw(args: {
  sinceMs: number;
  untilMs: number;
  limit: number;
  deviceId?: string;
}): TelemetryRow[] {
  const d = getDb();

  if (args.deviceId) {
    const stmt = d.prepare(
      "SELECT ts, temp, humidity, source_topic AS sourceTopic, device_id AS deviceId FROM sensor_readings WHERE ts >= ? AND ts <= ? AND device_id = ? ORDER BY ts ASC LIMIT ?"
    );
    return stmt.all(args.sinceMs, args.untilMs, args.deviceId, args.limit) as TelemetryRow[];
  }

  const stmt = d.prepare(
    "SELECT ts, temp, humidity, source_topic AS sourceTopic, device_id AS deviceId FROM sensor_readings WHERE ts >= ? AND ts <= ? ORDER BY ts ASC LIMIT ?"
  );
  return stmt.all(args.sinceMs, args.untilMs, args.limit) as TelemetryRow[];
}

export function queryHistoryBucketed(args: {
  sinceMs: number;
  untilMs: number;
  limit: number;
  bucketMs: number;
  deviceId?: string;
}): TelemetryBucketRow[] {
  const d = getDb();

  if (args.deviceId) {
    const stmt = d.prepare(
      `
      SELECT
        (CAST(ts / ? AS INTEGER) * ?) AS ts,
        AVG(temp) AS temp,
        AVG(humidity) AS humidity,
        COUNT(1) AS count,
        device_id AS deviceId
      FROM sensor_readings
      WHERE ts >= ? AND ts <= ? AND device_id = ?
      GROUP BY (CAST(ts / ? AS INTEGER) * ?)
      ORDER BY ts ASC
      LIMIT ?
    `
    );

    return stmt.all(args.bucketMs, args.bucketMs, args.sinceMs, args.untilMs, args.deviceId, args.bucketMs, args.bucketMs, args.limit) as TelemetryBucketRow[];
  }

  const stmt = d.prepare(
    `
    SELECT
      (CAST(ts / ? AS INTEGER) * ?) AS ts,
      AVG(temp) AS temp,
      AVG(humidity) AS humidity,
      COUNT(1) AS count,
      device_id AS deviceId
    FROM sensor_readings
    WHERE ts >= ? AND ts <= ?
    GROUP BY (CAST(ts / ? AS INTEGER) * ?)
    ORDER BY ts ASC
    LIMIT ?
  `
  );

  return stmt.all(args.bucketMs, args.bucketMs, args.sinceMs, args.untilMs, args.bucketMs, args.bucketMs, args.limit) as TelemetryBucketRow[];
}

// Actuator state management (stored in device capabilities)

export function updateActuatorState(deviceId: string, actuatorId: string, state: boolean): boolean {
  const d = getDb();
  const device = getDevice(deviceId);
  if (!device) return false;

  // Find and update the actuator in capabilities
  const actuatorIndex = device.capabilities.actuators.findIndex(a => a.id === actuatorId);
  if (actuatorIndex === -1) return false;

  device.capabilities.actuators[actuatorIndex].state = state;

  const stmt = d.prepare("UPDATE devices SET capabilities = ?, updated_at = ? WHERE id = ?");
  const result = stmt.run(JSON.stringify(device.capabilities), Date.now(), deviceId);
  return result.changes > 0;
}

export function getActuatorState(deviceId: string, actuatorId: string): boolean | null {
  const device = getDevice(deviceId);
  if (!device) return null;

  const actuator = device.capabilities.actuators.find(a => a.id === actuatorId);
  return actuator?.state ?? null;
}

export function updateActuatorName(deviceId: string, actuatorId: string, name: string): boolean {
  const d = getDb();
  const device = getDevice(deviceId);
  if (!device) return false;

  // Verify actuator exists in capabilities
  const actuator = device.capabilities.actuators.find(a => a.id === actuatorId);
  if (!actuator) return false;

  // Update actuator_names JSON
  const actuatorNames = { ...device.actuatorNames, [actuatorId]: name };

  const stmt = d.prepare("UPDATE devices SET actuator_names = ?, updated_at = ? WHERE id = ?");
  const result = stmt.run(JSON.stringify(actuatorNames), Date.now(), deviceId);
  return result.changes > 0;
}

export function removeActuatorName(deviceId: string, actuatorId: string): boolean {
  const d = getDb();
  const device = getDevice(deviceId);
  if (!device) return false;

  // Remove from actuator_names JSON
  const actuatorNames = { ...device.actuatorNames };
  delete actuatorNames[actuatorId];

  const stmt = d.prepare("UPDATE devices SET actuator_names = ?, updated_at = ? WHERE id = ?");
  const result = stmt.run(JSON.stringify(actuatorNames), Date.now(), deviceId);
  return result.changes > 0;
}

// Events

export function insertEvent(event: {
  ts: number;
  deviceId: string;
  eventType: string;
  payload?: unknown;
  source?: string;
}): Event {
  const d = getDb();
  const stmt = d.prepare(
    "INSERT INTO events (ts, device_id, event_type, payload, source) VALUES (?, ?, ?, ?, ?)"
  );
  const result = stmt.run(
    event.ts,
    event.deviceId,
    event.eventType,
    event.payload ? JSON.stringify(event.payload) : null,
    event.source ?? null
  );
  return {
    id: Number(result.lastInsertRowid),
    ts: event.ts,
    deviceId: event.deviceId,
    eventType: event.eventType,
    payload: event.payload ?? null,
    source: event.source ?? null,
  };
}

export function queryEvents(args: {
  sinceMs: number;
  untilMs?: number;
  deviceId?: string;
  eventType?: string;
  limit?: number;
}): Event[] {
  const d = getDb();
  const untilMs = args.untilMs ?? Date.now();
  const limit = args.limit ?? 100;

  let sql = "SELECT id, ts, device_id AS deviceId, event_type AS eventType, payload, source FROM events WHERE ts >= ? AND ts <= ?";
  const params: (string | number | null)[] = [args.sinceMs, untilMs];

  if (args.deviceId) {
    sql += " AND device_id = ?";
    params.push(args.deviceId);
  }
  if (args.eventType) {
    sql += " AND event_type = ?";
    params.push(args.eventType);
  }

  sql += " ORDER BY ts DESC LIMIT ?";
  params.push(limit);

  const stmt = d.prepare(sql);
  const rows = stmt.all(...params) as Array<Omit<Event, "payload"> & { payload: string | null }>;

  return rows.map((row) => ({
    ...row,
    payload: row.payload ? JSON.parse(row.payload) : null,
  }));
}

// Commands

const DEFAULT_COMMAND_TTL_MS = 30000; // 30 seconds

export function insertCommand(command: {
  id: string;
  ts: number;
  deviceId: string;
  target: string;
  action: string;
  value?: unknown;
  source: string;
  reason?: string;
  ttl?: number;
}): Command {
  const d = getDb();
  const stmt = d.prepare(
    "INSERT INTO commands (id, ts, device_id, target, action, value, source, reason, ttl, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')"
  );
  stmt.run(
    command.id,
    command.ts,
    command.deviceId,
    command.target,
    command.action,
    command.value !== undefined ? JSON.stringify(command.value) : null,
    command.source,
    command.reason ?? null,
    command.ttl ?? DEFAULT_COMMAND_TTL_MS
  );
  return getCommand(command.id)!;
}

export function getCommand(id: string): Command | null {
  const d = getDb();
  const stmt = d.prepare(
    "SELECT id, ts, device_id AS deviceId, target, action, value, source, reason, ttl, status, ack_ts AS ackTs, ack_payload AS ackPayload FROM commands WHERE id = ?"
  );
  const row = stmt.get(id) as (Omit<Command, "value" | "ackPayload"> & { value: string | null; ackPayload: string | null }) | undefined;
  if (!row) return null;
  return {
    ...row,
    value: row.value ? JSON.parse(row.value) : null,
    ackPayload: row.ackPayload ? JSON.parse(row.ackPayload) : null,
  };
}

export function updateCommandAck(
  id: string,
  ack: { status: Command["status"]; ackTs: number; ackPayload?: unknown }
): Command | null {
  const d = getDb();
  const stmt = d.prepare(
    "UPDATE commands SET status = ?, ack_ts = ?, ack_payload = ? WHERE id = ?"
  );
  stmt.run(
    ack.status,
    ack.ackTs,
    ack.ackPayload ? JSON.stringify(ack.ackPayload) : null,
    id
  );
  return getCommand(id);
}

export function queryCommands(args: {
  sinceMs: number;
  untilMs?: number;
  deviceId?: string;
  status?: Command["status"];
  limit?: number;
}): Command[] {
  const d = getDb();
  const untilMs = args.untilMs ?? Date.now();
  const limit = args.limit ?? 100;

  let sql = "SELECT id, ts, device_id AS deviceId, target, action, value, source, reason, ttl, status, ack_ts AS ackTs, ack_payload AS ackPayload FROM commands WHERE ts >= ? AND ts <= ?";
  const params: (string | number | null)[] = [args.sinceMs, untilMs];

  if (args.deviceId) {
    sql += " AND device_id = ?";
    params.push(args.deviceId);
  }
  if (args.status) {
    sql += " AND status = ?";
    params.push(args.status);
  }

  sql += " ORDER BY ts DESC LIMIT ?";
  params.push(limit);

  const stmt = d.prepare(sql);
  const rows = stmt.all(...params) as Array<Omit<Command, "value" | "ackPayload"> & { value: string | null; ackPayload: string | null }>;

  return rows.map((row) => ({
    ...row,
    value: row.value ? JSON.parse(row.value) : null,
    ackPayload: row.ackPayload ? JSON.parse(row.ackPayload) : null,
  }));
}

export function getPendingCommands(): Command[] {
  const d = getDb();
  const stmt = d.prepare(
    "SELECT id, ts, device_id AS deviceId, target, action, value, source, reason, ttl, status, ack_ts AS ackTs, ack_payload AS ackPayload FROM commands WHERE status = 'pending' ORDER BY ts ASC"
  );
  const rows = stmt.all() as Array<Omit<Command, "value" | "ackPayload"> & { value: string | null; ackPayload: string | null }>;

  return rows.map((row) => ({
    ...row,
    value: row.value ? JSON.parse(row.value) : null,
    ackPayload: row.ackPayload ? JSON.parse(row.ackPayload) : null,
  }));
}

export function expireCommands(): Command[] {
  const d = getDb();
  const now = Date.now();

  // Find commands that have exceeded their TTL
  const selectStmt = d.prepare(
    "SELECT id, ts, device_id AS deviceId, target, action, value, source, reason, ttl, status, ack_ts AS ackTs, ack_payload AS ackPayload FROM commands WHERE status = 'pending' AND (ts + ttl) < ?"
  );
  const expiredRows = selectStmt.all(now) as Array<Omit<Command, "value" | "ackPayload"> & { value: string | null; ackPayload: string | null }>;

  if (expiredRows.length === 0) {
    return [];
  }

  // Mark them as expired
  const updateStmt = d.prepare(
    "UPDATE commands SET status = 'expired', ack_ts = ? WHERE status = 'pending' AND (ts + ttl) < ?"
  );
  updateStmt.run(now, now);

  return expiredRows.map((row) => ({
    ...row,
    status: "expired" as const,
    ackTs: now,
    value: row.value ? JSON.parse(row.value) : null,
    ackPayload: row.ackPayload ? JSON.parse(row.ackPayload) : null,
  }));
}

// Devices

type DeviceDbRow = {
  id: string;
  location: string;
  name: string | null;
  platform: string | null;
  firmware: string | null;
  capabilities: string;
  actuator_names: string;
  telemetry_interval_ms: number | null;
  online: number;
  last_seen: number;
  created_at: number;
  updated_at: number;
};

function rowToDevice(row: DeviceDbRow): Device {
  return {
    id: row.id,
    location: row.location,
    name: row.name,
    platform: row.platform,
    firmware: row.firmware,
    capabilities: JSON.parse(row.capabilities) as DeviceCapabilities,
    actuatorNames: row.actuator_names ? JSON.parse(row.actuator_names) as Record<string, string> : {},
    telemetryIntervalMs: row.telemetry_interval_ms,
    online: Boolean(row.online),
    lastSeen: row.last_seen,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export function upsertDevice(device: {
  id: string;
  location: string;
  name?: string | null;
  platform?: string | null;
  firmware?: string | null;
  capabilities: DeviceCapabilities;
  telemetryIntervalMs?: number | null;
  online?: boolean;
  lastSeen: number;
}): Device {
  const d = getDb();
  const now = Date.now();
  const online = device.online ?? true;

  const existing = getDevice(device.id);
  if (existing) {
    const stmt = d.prepare(`
      UPDATE devices SET
        location = ?,
        name = COALESCE(?, name),
        platform = COALESCE(?, platform),
        firmware = COALESCE(?, firmware),
        capabilities = ?,
        telemetry_interval_ms = COALESCE(?, telemetry_interval_ms),
        online = ?,
        last_seen = ?,
        updated_at = ?
      WHERE id = ?
    `);
    stmt.run(
      device.location,
      device.name ?? null,
      device.platform ?? null,
      device.firmware ?? null,
      JSON.stringify(device.capabilities),
      device.telemetryIntervalMs ?? null,
      online ? 1 : 0,
      device.lastSeen,
      now,
      device.id
    );
  } else {
    const stmt = d.prepare(`
      INSERT INTO devices (id, location, name, platform, firmware, capabilities, telemetry_interval_ms, online, last_seen, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    stmt.run(
      device.id,
      device.location,
      device.name ?? null,
      device.platform ?? null,
      device.firmware ?? null,
      JSON.stringify(device.capabilities),
      device.telemetryIntervalMs ?? null,
      online ? 1 : 0,
      device.lastSeen,
      now,
      now
    );
  }

  return getDevice(device.id)!;
}

export function setDeviceOffline(deviceId: string): boolean {
  const d = getDb();
  const stmt = d.prepare("UPDATE devices SET online = 0, updated_at = ? WHERE id = ?");
  const result = stmt.run(Date.now(), deviceId);
  return result.changes > 0;
}

export function setDeviceOnline(deviceId: string): boolean {
  const d = getDb();
  const now = Date.now();
  const stmt = d.prepare("UPDATE devices SET online = 1, last_seen = ?, updated_at = ? WHERE id = ?");
  const result = stmt.run(now, now, deviceId);
  return result.changes > 0;
}

export function getDevice(deviceId: string): Device | null {
  const d = getDb();
  const stmt = d.prepare(`
    SELECT id, location, name, platform, firmware, capabilities, actuator_names, telemetry_interval_ms, online, last_seen, created_at, updated_at
    FROM devices WHERE id = ?
  `);
  const row = stmt.get(deviceId) as DeviceDbRow | undefined;
  if (!row) return null;
  return rowToDevice(row);
}

export function getAllDevices(): Device[] {
  const d = getDb();
  const stmt = d.prepare(`
    SELECT id, location, name, platform, firmware, capabilities, actuator_names, telemetry_interval_ms, online, last_seen, created_at, updated_at
    FROM devices ORDER BY location ASC, id ASC
  `);
  const rows = stmt.all() as DeviceDbRow[];
  return rows.map(rowToDevice);
}

export function getOnlineDevices(): Device[] {
  const d = getDb();
  const stmt = d.prepare(`
    SELECT id, location, name, platform, firmware, capabilities, actuator_names, telemetry_interval_ms, online, last_seen, created_at, updated_at
    FROM devices WHERE online = 1 ORDER BY location ASC, id ASC
  `);
  const rows = stmt.all() as DeviceDbRow[];
  return rows.map(rowToDevice);
}

export type DeviceActuator = Actuator & {
  deviceId: string;
  location: string;
  deviceOnline: boolean;
  customName?: string;
};

export function getDeviceActuators(deviceId?: string): DeviceActuator[] {
  const devices = deviceId ? [getDevice(deviceId)].filter(Boolean) as Device[] : getAllDevices();
  const actuators: DeviceActuator[] = [];

  for (const device of devices) {
    for (const actuator of device.capabilities.actuators) {
      actuators.push({
        ...actuator,
        deviceId: device.id,
        location: device.location,
        deviceOnline: device.online,
        customName: device.actuatorNames[actuator.id],
      });
    }
  }

  return actuators;
}
