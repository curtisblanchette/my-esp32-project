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
