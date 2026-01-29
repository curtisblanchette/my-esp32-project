import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

import { config } from "../config/index.js";

export type TelemetryRow = {
  ts: number;
  temp: number;
  humidity: number;
  sourceTopic: string | null;
};

export type TelemetryBucketRow = {
  ts: number;
  temp: number;
  humidity: number;
  count: number;
};

export type RelayConfig = {
  id: string;
  name: string;
  pin: number | null;
  enabled: boolean;
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
      source_topic TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_sensor_readings_ts ON sensor_readings(ts);

    CREATE TABLE IF NOT EXISTS relay_config (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      pin INTEGER,
      enabled INTEGER NOT NULL DEFAULT 1,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
  `);

  return db;
}

export function insertReading(row: TelemetryRow): void {
  const d = getDb();
  const stmt = d.prepare(
    "INSERT INTO sensor_readings (ts, temp, humidity, source_topic) VALUES (?, ?, ?, ?)"
  );
  stmt.run(row.ts, row.temp, row.humidity, row.sourceTopic);
}

export function queryHistoryRaw(args: {
  sinceMs: number;
  untilMs: number;
  limit: number;
}): TelemetryRow[] {
  const d = getDb();
  const stmt = d.prepare(
    "SELECT ts, temp, humidity, source_topic AS sourceTopic FROM sensor_readings WHERE ts >= ? AND ts <= ? ORDER BY ts ASC LIMIT ?"
  );
  return stmt.all(args.sinceMs, args.untilMs, args.limit) as TelemetryRow[];
}

export function queryHistoryBucketed(args: {
  sinceMs: number;
  untilMs: number;
  limit: number;
  bucketMs: number;
}): TelemetryBucketRow[] {
  const d = getDb();

  const stmt = d.prepare(
    `
    SELECT
      (CAST(ts / ? AS INTEGER) * ?) AS ts,
      AVG(temp) AS temp,
      AVG(humidity) AS humidity,
      COUNT(1) AS count
    FROM sensor_readings
    WHERE ts >= ? AND ts <= ?
    GROUP BY ts
    ORDER BY ts ASC
    LIMIT ?
  `
  );

  return stmt.all(args.bucketMs, args.bucketMs, args.sinceMs, args.untilMs, args.limit) as TelemetryBucketRow[];
}

export function getAllRelayConfigs(): RelayConfig[] {
  const d = getDb();
  const stmt = d.prepare(
    "SELECT id, name, pin, enabled, created_at AS createdAt, updated_at AS updatedAt FROM relay_config ORDER BY created_at ASC"
  );
  const rows = stmt.all() as Array<Omit<RelayConfig, 'enabled'> & { enabled: number }>;
  return rows.map(row => ({ ...row, enabled: Boolean(row.enabled) }));
}

export function getRelayConfig(id: string): RelayConfig | null {
  const d = getDb();
  const stmt = d.prepare(
    "SELECT id, name, pin, enabled, created_at AS createdAt, updated_at AS updatedAt FROM relay_config WHERE id = ?"
  );
  const result = stmt.get(id) as (Omit<RelayConfig, 'enabled'> & { enabled: number }) | undefined;
  if (!result) return null;
  return { ...result, enabled: Boolean(result.enabled) };
}

export function createRelayConfig(config: { id: string; name: string; pin?: number | null; enabled?: boolean }): RelayConfig {
  const d = getDb();
  const now = Date.now();
  const stmt = d.prepare(
    "INSERT INTO relay_config (id, name, pin, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
  );
  stmt.run(config.id, config.name, config.pin ?? null, config.enabled ? 1 : 0, now, now);
  return getRelayConfig(config.id)!;
}

export function updateRelayConfig(id: string, updates: { name?: string; pin?: number | null; enabled?: boolean }): RelayConfig | null {
  const d = getDb();
  const existing = getRelayConfig(id);
  if (!existing) return null;

  const now = Date.now();
  const name = updates.name ?? existing.name;
  const pin = updates.pin !== undefined ? updates.pin : existing.pin;
  const enabled = updates.enabled !== undefined ? updates.enabled : existing.enabled;

  const stmt = d.prepare(
    "UPDATE relay_config SET name = ?, pin = ?, enabled = ?, updated_at = ? WHERE id = ?"
  );
  stmt.run(name, pin, enabled ? 1 : 0, now, id);
  return getRelayConfig(id);
}

export function deleteRelayConfig(id: string): boolean {
  const d = getDb();
  const stmt = d.prepare("DELETE FROM relay_config WHERE id = ?");
  const result = stmt.run(id);
  return result.changes > 0;
}
