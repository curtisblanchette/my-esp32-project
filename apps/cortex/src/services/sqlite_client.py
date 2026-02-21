"""
SQLite client for telemetry storage, device registry, commands, and events.

Ported from apps/api/src/lib/sqlite.ts — exact same schema and query logic
to maintain frontend compatibility.
"""

import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Types

@dataclass
class SensorValue:
    ts: int
    device_id: str
    sensor_id: str
    value: float
    source_topic: str | None = None


@dataclass
class Event:
    id: int
    ts: int
    device_id: str
    event_type: str
    payload: Any = None
    source: str | None = None


@dataclass
class Command:
    id: str  # correlationId
    ts: int
    device_id: str
    target: str
    action: str
    value: Any = None
    source: str = "unknown"
    reason: str | None = None
    ttl: int = 30000
    status: str = "pending"
    ack_ts: int | None = None
    ack_payload: Any = None


@dataclass
class Actuator:
    id: str
    type: str
    pin: int | None = None
    name: str | None = None
    state: bool | None = None


@dataclass
class Sensor:
    id: str
    type: str
    name: str | None = None
    unit: str | None = None


@dataclass
class DeviceCapabilities:
    sensors: list[Sensor] = field(default_factory=list)
    actuators: list[Actuator] = field(default_factory=list)


@dataclass
class Device:
    id: str
    location: str
    name: str | None = None
    platform: str | None = None
    firmware: str | None = None
    capabilities: DeviceCapabilities = field(default_factory=DeviceCapabilities)
    actuator_names: dict[str, str] = field(default_factory=dict)
    telemetry_interval_ms: int | None = None
    online: bool = True
    last_seen: int = 0
    created_at: int = 0
    updated_at: int = 0
    display_order: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict matching the Node.js API response shape (camelCase)."""
        return {
            "id": self.id,
            "location": self.location,
            "name": self.name,
            "platform": self.platform,
            "firmware": self.firmware,
            "capabilities": {
                "sensors": [
                    {"id": s.id, "type": s.type,
                     **({"name": s.name} if s.name else {}),
                     **({"unit": s.unit} if s.unit else {})}
                    for s in self.capabilities.sensors
                ],
                "actuators": [
                    {
                        "id": a.id,
                        "type": a.type,
                        **({"pin": a.pin} if a.pin is not None else {}),
                        **({"name": a.name} if a.name else {}),
                        **({"state": a.state} if a.state is not None else {}),
                    }
                    for a in self.capabilities.actuators
                ],
            },
            "actuatorNames": self.actuator_names,
            "telemetryIntervalMs": self.telemetry_interval_ms,
            "online": self.online,
            "lastSeen": self.last_seen,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "displayOrder": self.display_order,
        }


@dataclass
class DeviceActuator:
    id: str
    type: str
    pin: int | None = None
    name: str | None = None
    state: bool | None = None
    device_id: str = ""
    location: str = ""
    device_online: bool = False
    custom_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            **({"pin": self.pin} if self.pin is not None else {}),
            **({"name": self.name} if self.name else {}),
            **({"state": self.state} if self.state is not None else {}),
            "deviceId": self.device_id,
            "location": self.location,
            "deviceOnline": self.device_online,
            **({"customName": self.custom_name} if self.custom_name else {}),
        }


DEFAULT_COMMAND_TTL_MS = 30000


class SqliteClient:
    """SQLite database client for telemetry, devices, commands, and events."""

    def __init__(self, db_path: str, journal_mode: str = "WAL"):
        self._db_path = db_path
        self._journal_mode = journal_mode
        self._db: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Initialize the database connection and create tables."""
        db_path = os.path.abspath(self._db_path)
        logger.info(f"SQLite DB path: {db_path}")
        logger.info(f"SQLite journal_mode: {self._journal_mode}")

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._db.execute("PRAGMA foreign_keys = ON")

        self._db.executescript(f"""
            PRAGMA journal_mode = {self._journal_mode};
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
                actuator_names JSON NOT NULL DEFAULT '{json.dumps({})}',
                telemetry_interval_ms INTEGER,
                online INTEGER NOT NULL DEFAULT 1,
                last_seen INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_devices_location ON devices(location);
            CREATE INDEX IF NOT EXISTS idx_devices_online ON devices(online);

            CREATE TABLE IF NOT EXISTS location_goals (
                location TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sensor_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                sensor_id TEXT NOT NULL,
                value REAL NOT NULL,
                source_topic TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sv_ts ON sensor_values(ts);
            CREATE INDEX IF NOT EXISTS idx_sv_device_sensor ON sensor_values(device_id, sensor_id, ts);
            CREATE INDEX IF NOT EXISTS idx_sv_device_ts ON sensor_values(device_id, ts);

            CREATE TABLE IF NOT EXISTS cortex_profiles (
                id TEXT PRIMARY KEY,
                location TEXT NOT NULL,
                name TEXT NOT NULL,
                strategy TEXT NOT NULL DEFAULT 'balanced',
                phase TEXT,
                phase_start TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(location)
            );
            CREATE INDEX IF NOT EXISTS idx_profiles_location ON cortex_profiles(location);

            CREATE TABLE IF NOT EXISTS cortex_goals (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL REFERENCES cortex_profiles(id) ON DELETE CASCADE,
                metric TEXT NOT NULL,
                metric_type TEXT NOT NULL DEFAULT 'sensor',
                phase TEXT,
                range_min REAL,
                range_max REAL,
                tolerance REAL DEFAULT 0.0,
                priority REAL DEFAULT 1.0,
                schedule TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_goals_profile ON cortex_goals(profile_id);

            CREATE TABLE IF NOT EXISTS cortex_health (
                location TEXT NOT NULL,
                ts INTEGER NOT NULL,
                score REAL NOT NULL,
                detail TEXT NOT NULL,
                PRIMARY KEY (location, ts)
            );
            CREATE INDEX IF NOT EXISTS idx_health_location_ts ON cortex_health(location, ts);

            CREATE TABLE IF NOT EXISTS cortex_effects (
                device_id TEXT NOT NULL,
                actuator TEXT NOT NULL,
                action TEXT NOT NULL,
                sensor TEXT NOT NULL,
                avg_delta_5m REAL NOT NULL,
                std_dev REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                sum_values REAL NOT NULL,
                sum_squares REAL NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (device_id, actuator, action, sensor)
            );

        """)

        self._run_migrations()
        logger.info("SQLite initialized")

    def _run_migrations(self) -> None:
        """Run schema migrations for existing databases."""
        db = self._get_db()
        cursor = db.execute("PRAGMA table_info(commands)")
        columns = {row["name"] for row in cursor.fetchall()}

        if "ttl" not in columns:
            logger.info("Migration: Adding ttl column to commands table")
            db.execute("ALTER TABLE commands ADD COLUMN ttl INTEGER NOT NULL DEFAULT 30000")

        cursor = db.execute("PRAGMA table_info(sensor_readings)")
        columns = {row["name"] for row in cursor.fetchall()}

        if "device_id" not in columns:
            logger.info("Migration: Adding device_id column to sensor_readings table")
            db.execute("ALTER TABLE sensor_readings ADD COLUMN device_id TEXT")

        db.execute("CREATE INDEX IF NOT EXISTS idx_sensor_readings_device ON sensor_readings(device_id, ts)")

        cursor = db.execute("PRAGMA table_info(devices)")
        columns = {row["name"] for row in cursor.fetchall()}

        if "actuator_names" not in columns:
            logger.info("Migration: Adding actuator_names column to devices table")
            db.execute("ALTER TABLE devices ADD COLUMN actuator_names JSON NOT NULL DEFAULT '{}'")

        if "display_order" not in columns:
            logger.info("Migration: Adding display_order column to devices table")
            db.execute("ALTER TABLE devices ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0")

        db.execute("DROP TABLE IF EXISTS relay_config")
        db.execute("UPDATE sensor_readings SET device_id = 'esp32-1' WHERE device_id IS NULL")

        # Migrate sensor_readings → sensor_values (one-time)
        sv_count = db.execute("SELECT COUNT(*) FROM sensor_values").fetchone()[0]
        sr_count = db.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0]
        if sv_count == 0 and sr_count > 0:
            logger.info(f"Migration: Converting {sr_count} sensor_readings to sensor_values")
            db.execute(
                "INSERT INTO sensor_values (ts, device_id, sensor_id, value, source_topic) "
                "SELECT ts, COALESCE(device_id, 'esp32-1'), 'temp1', temp, source_topic "
                "FROM sensor_readings"
            )
            db.execute(
                "INSERT INTO sensor_values (ts, device_id, sensor_id, value, source_topic) "
                "SELECT ts, COALESCE(device_id, 'esp32-1'), 'hum1', humidity, source_topic "
                "FROM sensor_readings"
            )
            logger.info("Migration: sensor_values populated")

        # Add time_window columns to cortex_goals for day/night goal scheduling
        cursor = db.execute("PRAGMA table_info(cortex_goals)")
        goal_columns = {row["name"] for row in cursor.fetchall()}
        if "time_window_on_hour" not in goal_columns:
            logger.info("Migration: Adding time_window columns to cortex_goals table")
            db.execute("ALTER TABLE cortex_goals ADD COLUMN time_window_on_hour REAL")
            db.execute("ALTER TABLE cortex_goals ADD COLUMN time_window_off_hour REAL")

        db.commit()

    def _get_db(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None

    # ── Sensor Values (generic EAV) ─────────────────────────────────

    def insert_sensor_values(self, values: list[SensorValue]) -> None:
        """Bulk insert sensor values (one row per sensor per timestamp)."""
        if not values:
            return
        db = self._get_db()
        db.executemany(
            "INSERT INTO sensor_values (ts, device_id, sensor_id, value, source_topic) "
            "VALUES (?, ?, ?, ?, ?)",
            [(v.ts, v.device_id, v.sensor_id, v.value, v.source_topic) for v in values],
        )
        db.commit()

    def query_sensor_values(
        self,
        since_ms: int,
        until_ms: int,
        device_id: str | None = None,
        sensor_id: str | None = None,
        limit: int = 5000,
    ) -> list[SensorValue]:
        """Query generic sensor values, optionally filtered by device and sensor."""
        db = self._get_db()
        sql = "SELECT ts, device_id, sensor_id, value, source_topic FROM sensor_values WHERE ts >= ? AND ts <= ?"
        params: list[Any] = [since_ms, until_ms]

        if device_id:
            sql += " AND device_id = ?"
            params.append(device_id)
        if sensor_id:
            sql += " AND sensor_id = ?"
            params.append(sensor_id)

        sql += " ORDER BY ts ASC LIMIT ?"
        params.append(limit)

        cursor = db.execute(sql, params)
        return [
            SensorValue(
                ts=row["ts"],
                device_id=row["device_id"],
                sensor_id=row["sensor_id"],
                value=row["value"],
                source_topic=row["source_topic"],
            )
            for row in cursor.fetchall()
        ]

    def query_sensor_values_bucketed(
        self,
        since_ms: int,
        until_ms: int,
        bucket_ms: int,
        device_id: str | None = None,
        sensor_id: str | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Query bucketed (time-averaged) sensor values. Returns list of dicts."""
        db = self._get_db()
        sql = """
            SELECT
                (CAST(ts / ? AS INTEGER) * ?) AS bucket_ts,
                device_id,
                sensor_id,
                AVG(value) AS value,
                COUNT(1) AS count
            FROM sensor_values
            WHERE ts >= ? AND ts <= ?
        """
        params: list[Any] = [bucket_ms, bucket_ms, since_ms, until_ms]

        if device_id:
            sql += " AND device_id = ?"
            params.append(device_id)
        if sensor_id:
            sql += " AND sensor_id = ?"
            params.append(sensor_id)

        sql += " GROUP BY bucket_ts, device_id, sensor_id ORDER BY bucket_ts ASC LIMIT ?"
        params.append(limit)

        cursor = db.execute(sql, params)
        return [
            {
                "ts": row["bucket_ts"],
                "device_id": row["device_id"],
                "sensor_id": row["sensor_id"],
                "value": row["value"],
                "count": row["count"],
            }
            for row in cursor.fetchall()
        ]

    # ── Actuator State ───────────────────────────────────────────────

    def update_actuator_state(self, device_id: str, actuator_id: str, state: bool) -> bool:
        device = self.get_device(device_id)
        if not device:
            return False

        actuator_idx = None
        for i, a in enumerate(device.capabilities.actuators):
            if a.id == actuator_id:
                actuator_idx = i
                break

        if actuator_idx is None:
            return False

        device.capabilities.actuators[actuator_idx].state = state
        caps_json = json.dumps({
            "sensors": [{"id": s.id, "type": s.type, **({"name": s.name} if s.name else {}), **({"unit": s.unit} if s.unit else {})} for s in device.capabilities.sensors],
            "actuators": [
                {
                    "id": a.id, "type": a.type,
                    **({"pin": a.pin} if a.pin is not None else {}),
                    **({"name": a.name} if a.name else {}),
                    **({"state": a.state} if a.state is not None else {}),
                }
                for a in device.capabilities.actuators
            ],
        })

        db = self._get_db()
        cursor = db.execute(
            "UPDATE devices SET capabilities = ?, updated_at = ? WHERE id = ?",
            (caps_json, int(time.time() * 1000), device_id),
        )
        db.commit()
        return cursor.rowcount > 0

    def get_actuator_state(self, device_id: str, actuator_id: str) -> bool | None:
        device = self.get_device(device_id)
        if not device:
            return None
        for a in device.capabilities.actuators:
            if a.id == actuator_id:
                return a.state
        return None

    def update_actuator_name(self, device_id: str, actuator_id: str, name: str) -> bool:
        device = self.get_device(device_id)
        if not device:
            return False

        if not any(a.id == actuator_id for a in device.capabilities.actuators):
            return False

        actuator_names = {**device.actuator_names, actuator_id: name}
        db = self._get_db()
        cursor = db.execute(
            "UPDATE devices SET actuator_names = ?, updated_at = ? WHERE id = ?",
            (json.dumps(actuator_names), int(time.time() * 1000), device_id),
        )
        db.commit()
        return cursor.rowcount > 0

    def remove_actuator_name(self, device_id: str, actuator_id: str) -> bool:
        device = self.get_device(device_id)
        if not device:
            return False

        actuator_names = {k: v for k, v in device.actuator_names.items() if k != actuator_id}
        db = self._get_db()
        cursor = db.execute(
            "UPDATE devices SET actuator_names = ?, updated_at = ? WHERE id = ?",
            (json.dumps(actuator_names), int(time.time() * 1000), device_id),
        )
        db.commit()
        return cursor.rowcount > 0

    # ── Events ───────────────────────────────────────────────────────

    def insert_event(
        self,
        ts: int,
        device_id: str,
        event_type: str,
        payload: Any = None,
        source: str | None = None,
    ) -> Event:
        db = self._get_db()
        cursor = db.execute(
            "INSERT INTO events (ts, device_id, event_type, payload, source) VALUES (?, ?, ?, ?, ?)",
            (ts, device_id, event_type, json.dumps(payload) if payload else None, source),
        )
        db.commit()
        return Event(
            id=cursor.lastrowid,
            ts=ts,
            device_id=device_id,
            event_type=event_type,
            payload=payload,
            source=source,
        )

    def query_events(
        self,
        since_ms: int,
        until_ms: int | None = None,
        device_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        db = self._get_db()
        until_ms = until_ms or int(time.time() * 1000)

        sql = "SELECT id, ts, device_id, event_type, payload, source FROM events WHERE ts >= ? AND ts <= ?"
        params: list[Any] = [since_ms, until_ms]

        if device_id:
            sql += " AND device_id = ?"
            params.append(device_id)
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)

        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        cursor = db.execute(sql, params)
        return [
            Event(
                id=row["id"],
                ts=row["ts"],
                device_id=row["device_id"],
                event_type=row["event_type"],
                payload=json.loads(row["payload"]) if row["payload"] else None,
                source=row["source"],
            )
            for row in cursor.fetchall()
        ]

    # ── Commands ─────────────────────────────────────────────────────

    def insert_command(
        self,
        id: str,
        ts: int,
        device_id: str,
        target: str,
        action: str,
        value: Any = None,
        source: str = "unknown",
        reason: str | None = None,
        ttl: int | None = None,
    ) -> Command:
        db = self._get_db()
        db.execute(
            "INSERT INTO commands (id, ts, device_id, target, action, value, source, reason, ttl, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (
                id, ts, device_id, target, action,
                json.dumps(value) if value is not None else None,
                source, reason, ttl or DEFAULT_COMMAND_TTL_MS,
            ),
        )
        db.commit()
        return self.get_command(id)

    def get_command(self, id: str) -> Command | None:
        db = self._get_db()
        cursor = db.execute(
            "SELECT id, ts, device_id, target, action, value, source, reason, ttl, status, ack_ts, ack_payload "
            "FROM commands WHERE id = ?",
            (id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_command(row)

    def update_command_ack(self, id: str, status: str, ack_ts: int, ack_payload: Any = None) -> Command | None:
        db = self._get_db()
        db.execute(
            "UPDATE commands SET status = ?, ack_ts = ?, ack_payload = ? WHERE id = ?",
            (status, ack_ts, json.dumps(ack_payload) if ack_payload else None, id),
        )
        db.commit()
        return self.get_command(id)

    def query_commands(
        self,
        since_ms: int,
        until_ms: int | None = None,
        device_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Command]:
        db = self._get_db()
        until_ms = until_ms or int(time.time() * 1000)

        sql = ("SELECT id, ts, device_id, target, action, value, source, reason, ttl, status, ack_ts, ack_payload "
               "FROM commands WHERE ts >= ? AND ts <= ?")
        params: list[Any] = [since_ms, until_ms]

        if device_id:
            sql += " AND device_id = ?"
            params.append(device_id)
        if status:
            sql += " AND status = ?"
            params.append(status)

        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        cursor = db.execute(sql, params)
        return [self._row_to_command(row) for row in cursor.fetchall()]

    def has_pending_command_for_target(self, device_id: str, target: str) -> bool:
        db = self._get_db()
        cursor = db.execute(
            "SELECT 1 FROM commands WHERE device_id = ? AND target = ? AND status = 'pending' LIMIT 1",
            (device_id, target),
        )
        return cursor.fetchone() is not None

    def get_pending_commands(self) -> list[Command]:
        db = self._get_db()
        cursor = db.execute(
            "SELECT id, ts, device_id, target, action, value, source, reason, ttl, status, ack_ts, ack_payload "
            "FROM commands WHERE status = 'pending' ORDER BY ts ASC"
        )
        return [self._row_to_command(row) for row in cursor.fetchall()]

    def expire_commands(self) -> list[Command]:
        db = self._get_db()
        now = int(time.time() * 1000)

        cursor = db.execute(
            "SELECT id, ts, device_id, target, action, value, source, reason, ttl, status, ack_ts, ack_payload "
            "FROM commands WHERE status = 'pending' AND (ts + ttl) < ?",
            (now,),
        )
        expired_rows = cursor.fetchall()

        if not expired_rows:
            return []

        db.execute(
            "UPDATE commands SET status = 'expired', ack_ts = ? WHERE status = 'pending' AND (ts + ttl) < ?",
            (now, now),
        )
        db.commit()

        return [
            Command(
                id=row["id"],
                ts=row["ts"],
                device_id=row["device_id"],
                target=row["target"],
                action=row["action"],
                value=json.loads(row["value"]) if row["value"] else None,
                source=row["source"],
                reason=row["reason"],
                ttl=row["ttl"],
                status="expired",
                ack_ts=now,
                ack_payload=json.loads(row["ack_payload"]) if row["ack_payload"] else None,
            )
            for row in expired_rows
        ]

    def _row_to_command(self, row: sqlite3.Row) -> Command:
        return Command(
            id=row["id"],
            ts=row["ts"],
            device_id=row["device_id"],
            target=row["target"],
            action=row["action"],
            value=json.loads(row["value"]) if row["value"] else None,
            source=row["source"],
            reason=row["reason"],
            ttl=row["ttl"],
            status=row["status"],
            ack_ts=row["ack_ts"],
            ack_payload=json.loads(row["ack_payload"]) if row["ack_payload"] else None,
        )

    def command_to_dict(self, cmd: Command) -> dict[str, Any]:
        """Serialize a Command to dict matching Node.js API response (camelCase)."""
        return {
            "id": cmd.id,
            "ts": cmd.ts,
            "deviceId": cmd.device_id,
            "target": cmd.target,
            "action": cmd.action,
            "value": cmd.value,
            "source": cmd.source,
            "reason": cmd.reason,
            "ttl": cmd.ttl,
            "status": cmd.status,
            "ackTs": cmd.ack_ts,
            "ackPayload": cmd.ack_payload,
        }

    def event_to_dict(self, evt: Event) -> dict[str, Any]:
        """Serialize an Event matching the WebSocket broadcast shape."""
        return {
            "id": str(evt.id),
            "ts": evt.ts,
            "deviceId": evt.device_id,
            "eventType": evt.event_type,
            "data": evt.payload,
            "source": evt.source,
        }

    # ── Devices ──────────────────────────────────────────────────────

    def upsert_device(
        self,
        id: str,
        location: str,
        capabilities: dict[str, Any] | None = None,
        name: str | None = None,
        platform: str | None = None,
        firmware: str | None = None,
        telemetry_interval_ms: int | None = None,
        online: bool = True,
        last_seen: int | None = None,
    ) -> Device:
        db = self._get_db()
        now = int(time.time() * 1000)
        last_seen = last_seen or now
        caps_json = json.dumps(capabilities or {"sensors": [], "actuators": []})

        existing = self.get_device(id)
        if existing:
            db.execute(
                """
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
                """,
                (location, name, platform, firmware, caps_json,
                 telemetry_interval_ms, 1 if online else 0, last_seen, now, id),
            )
        else:
            db.execute(
                """
                INSERT INTO devices (id, location, name, platform, firmware, capabilities,
                    telemetry_interval_ms, online, last_seen, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (id, location, name, platform, firmware, caps_json,
                 telemetry_interval_ms, 1 if online else 0, last_seen, now, now),
            )
        db.commit()
        return self.get_device(id)

    def set_device_offline(self, device_id: str) -> bool:
        db = self._get_db()
        cursor = db.execute(
            "UPDATE devices SET online = 0, updated_at = ? WHERE id = ?",
            (int(time.time() * 1000), device_id),
        )
        db.commit()
        return cursor.rowcount > 0

    def get_device(self, device_id: str) -> Device | None:
        db = self._get_db()
        cursor = db.execute(
            "SELECT id, location, name, platform, firmware, capabilities, actuator_names, "
            "telemetry_interval_ms, online, last_seen, created_at, updated_at, display_order "
            "FROM devices WHERE id = ?",
            (device_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_device(row)

    def get_all_devices(self) -> list[Device]:
        db = self._get_db()
        cursor = db.execute(
            "SELECT id, location, name, platform, firmware, capabilities, actuator_names, "
            "telemetry_interval_ms, online, last_seen, created_at, updated_at, display_order "
            "FROM devices ORDER BY display_order ASC, location ASC, id ASC"
        )
        return [self._row_to_device(row) for row in cursor.fetchall()]

    def get_online_devices(self) -> list[Device]:
        db = self._get_db()
        cursor = db.execute(
            "SELECT id, location, name, platform, firmware, capabilities, actuator_names, "
            "telemetry_interval_ms, online, last_seen, created_at, updated_at, display_order "
            "FROM devices WHERE online = 1 ORDER BY display_order ASC, location ASC, id ASC"
        )
        return [self._row_to_device(row) for row in cursor.fetchall()]

    def reorder_devices(self, orders: list[dict[str, Any]]) -> None:
        db = self._get_db()
        now = int(time.time() * 1000)
        for item in orders:
            db.execute(
                "UPDATE devices SET display_order = ?, updated_at = ? WHERE id = ?",
                (item["order"], now, item["id"]),
            )
        db.commit()

    def get_device_actuators(self, device_id: str | None = None) -> list[DeviceActuator]:
        devices = [self.get_device(device_id)] if device_id else self.get_all_devices()
        devices = [d for d in devices if d is not None]
        actuators: list[DeviceActuator] = []

        for device in devices:
            for actuator in device.capabilities.actuators:
                actuators.append(DeviceActuator(
                    id=actuator.id,
                    type=actuator.type,
                    pin=actuator.pin,
                    name=actuator.name,
                    state=actuator.state,
                    device_id=device.id,
                    location=device.location,
                    device_online=device.online,
                    custom_name=device.actuator_names.get(actuator.id),
                ))

        return actuators

    # ── Grow Profiles ──────────────────────────────────────────────

    VALID_STRATEGIES = {"precision", "balanced", "efficiency"}
    VALID_PHASES = {"seedling", "veg", "flower", "late_flower", "dry", "cure"}

    def insert_profile(
        self,
        location: str,
        name: str,
        strategy: str = "balanced",
        phase: str | None = None,
        phase_start: str | None = None,
    ) -> dict:
        """Create a new grow profile for a location."""
        db = self._get_db()
        profile_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        db.execute(
            "INSERT INTO cortex_profiles (id, location, name, strategy, phase, phase_start, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (profile_id, location, name, strategy, phase, phase_start, now, now),
        )
        db.commit()
        return self.get_profile(profile_id)

    def update_profile(
        self,
        profile_id: str,
        name: str | None = None,
        strategy: str | None = None,
        phase: str | None = None,
        phase_start: str | None = None,
        active: bool | None = None,
    ) -> dict | None:
        """Update a profile. Only non-None fields are updated."""
        db = self._get_db()
        sets: list[str] = []
        params: list[Any] = []

        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if strategy is not None:
            sets.append("strategy = ?")
            params.append(strategy)
        if phase is not None:
            sets.append("phase = ?")
            params.append(phase)
        if phase_start is not None:
            sets.append("phase_start = ?")
            params.append(phase_start)
        if active is not None:
            sets.append("active = ?")
            params.append(1 if active else 0)

        if not sets:
            return self.get_profile(profile_id)

        sets.append("updated_at = ?")
        params.append(int(time.time() * 1000))
        params.append(profile_id)

        cursor = db.execute(
            f"UPDATE cortex_profiles SET {', '.join(sets)} WHERE id = ?", params
        )
        db.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_profile(profile_id)

    def get_profile(self, profile_id: str) -> dict | None:
        db = self._get_db()
        row = db.execute("SELECT * FROM cortex_profiles WHERE id = ?", (profile_id,)).fetchone()
        if not row:
            return None
        return self._profile_row_to_dict(row)

    def get_profile_by_location(self, location: str) -> dict | None:
        db = self._get_db()
        row = db.execute(
            "SELECT * FROM cortex_profiles WHERE location = ? AND active = 1", (location,)
        ).fetchone()
        if not row:
            return None
        return self._profile_row_to_dict(row)

    def get_all_profiles(self) -> list[dict]:
        db = self._get_db()
        rows = db.execute("SELECT * FROM cortex_profiles ORDER BY location ASC").fetchall()
        return [self._profile_row_to_dict(row) for row in rows]

    def delete_profile(self, profile_id: str) -> bool:
        db = self._get_db()
        cursor = db.execute("DELETE FROM cortex_profiles WHERE id = ?", (profile_id,))
        db.commit()
        return cursor.rowcount > 0

    def _profile_row_to_dict(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "location": row["location"],
            "name": row["name"],
            "strategy": row["strategy"],
            "phase": row["phase"],
            "phaseStart": row["phase_start"],
            "active": bool(row["active"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    # ── Goals ────────────────────────────────────────────────────────

    def insert_goal(
        self,
        profile_id: str,
        metric: str,
        metric_type: str = "sensor",
        phase: str | None = None,
        range_min: float | None = None,
        range_max: float | None = None,
        tolerance: float = 0.0,
        priority: float = 1.0,
        schedule: dict | None = None,
        time_window_on_hour: float | None = None,
        time_window_off_hour: float | None = None,
    ) -> dict:
        """Create a new goal for a profile."""
        db = self._get_db()
        goal_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        db.execute(
            "INSERT INTO cortex_goals (id, profile_id, metric, metric_type, phase, "
            "range_min, range_max, tolerance, priority, schedule, "
            "time_window_on_hour, time_window_off_hour, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (goal_id, profile_id, metric, metric_type, phase,
             range_min, range_max, tolerance, priority,
             json.dumps(schedule) if schedule else None,
             time_window_on_hour, time_window_off_hour, now, now),
        )
        db.commit()
        return self.get_goal(goal_id)

    _UNSET = object()

    def update_goal(
        self,
        goal_id: str,
        metric: str | None = None,
        metric_type: str | None = None,
        phase: str | None = None,
        range_min: float | None = None,
        range_max: float | None = None,
        tolerance: float | None = None,
        priority: float | None = None,
        schedule: dict | None = None,
        time_window_on_hour: float | None | object = _UNSET,
        time_window_off_hour: float | None | object = _UNSET,
    ) -> dict | None:
        db = self._get_db()
        sets: list[str] = []
        params: list[Any] = []

        if metric is not None:
            sets.append("metric = ?")
            params.append(metric)
        if metric_type is not None:
            sets.append("metric_type = ?")
            params.append(metric_type)
        if phase is not None:
            sets.append("phase = ?")
            params.append(phase)
        if range_min is not None:
            sets.append("range_min = ?")
            params.append(range_min)
        if range_max is not None:
            sets.append("range_max = ?")
            params.append(range_max)
        if tolerance is not None:
            sets.append("tolerance = ?")
            params.append(tolerance)
        if priority is not None:
            sets.append("priority = ?")
            params.append(priority)
        if schedule is not None:
            sets.append("schedule = ?")
            params.append(json.dumps(schedule))
        if time_window_on_hour is not self._UNSET:
            sets.append("time_window_on_hour = ?")
            params.append(time_window_on_hour)
        if time_window_off_hour is not self._UNSET:
            sets.append("time_window_off_hour = ?")
            params.append(time_window_off_hour)

        if not sets:
            return self.get_goal(goal_id)

        sets.append("updated_at = ?")
        params.append(int(time.time() * 1000))
        params.append(goal_id)

        cursor = db.execute(
            f"UPDATE cortex_goals SET {', '.join(sets)} WHERE id = ?", params
        )
        db.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_goal(goal_id)

    def get_goal(self, goal_id: str) -> dict | None:
        db = self._get_db()
        row = db.execute("SELECT * FROM cortex_goals WHERE id = ?", (goal_id,)).fetchone()
        if not row:
            return None
        return self._goal_row_to_dict(row)

    def get_goals_for_profile(self, profile_id: str, phase: str | None = None) -> list[dict]:
        """Get goals for a profile, optionally filtered to a specific phase."""
        db = self._get_db()
        if phase:
            # Return goals matching the phase OR goals with no phase (all-phases)
            cursor = db.execute(
                "SELECT * FROM cortex_goals WHERE profile_id = ? AND (phase = ? OR phase IS NULL) "
                "ORDER BY priority DESC",
                (profile_id, phase),
            )
        else:
            cursor = db.execute(
                "SELECT * FROM cortex_goals WHERE profile_id = ? ORDER BY priority DESC",
                (profile_id,),
            )
        return [self._goal_row_to_dict(row) for row in cursor.fetchall()]

    def delete_goal(self, goal_id: str) -> bool:
        db = self._get_db()
        cursor = db.execute("DELETE FROM cortex_goals WHERE id = ?", (goal_id,))
        db.commit()
        return cursor.rowcount > 0

    def delete_goals_for_profile(self, profile_id: str) -> int:
        db = self._get_db()
        cursor = db.execute("DELETE FROM cortex_goals WHERE profile_id = ?", (profile_id,))
        db.commit()
        return cursor.rowcount

    def _goal_row_to_dict(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "profileId": row["profile_id"],
            "metric": row["metric"],
            "metricType": row["metric_type"],
            "phase": row["phase"],
            "rangeMin": row["range_min"],
            "rangeMax": row["range_max"],
            "tolerance": row["tolerance"],
            "priority": row["priority"],
            "schedule": json.loads(row["schedule"]) if row["schedule"] else None,
            "timeWindow": {
                "onHour": row["time_window_on_hour"],
                "offHour": row["time_window_off_hour"],
            } if row["time_window_on_hour"] is not None else None,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    # ── Ecosystem Health ─────────────────────────────────────────────

    def insert_health(self, location: str, ts: int, score: float, detail: dict) -> None:
        """Record an ecosystem health snapshot."""
        db = self._get_db()
        db.execute(
            "INSERT OR REPLACE INTO cortex_health (location, ts, score, detail) VALUES (?, ?, ?, ?)",
            (location, ts, score, json.dumps(detail)),
        )
        db.commit()

    def query_health(
        self,
        location: str,
        since_ms: int,
        until_ms: int | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Query health history for a location."""
        db = self._get_db()
        until_ms = until_ms or int(time.time() * 1000)
        cursor = db.execute(
            "SELECT location, ts, score, detail FROM cortex_health "
            "WHERE location = ? AND ts >= ? AND ts <= ? ORDER BY ts ASC LIMIT ?",
            (location, since_ms, until_ms, limit),
        )
        return [
            {
                "location": row["location"],
                "ts": row["ts"],
                "score": row["score"],
                "detail": json.loads(row["detail"]),
            }
            for row in cursor.fetchall()
        ]

    def get_latest_health(self, location: str) -> dict | None:
        """Get the most recent health snapshot for a location."""
        db = self._get_db()
        row = db.execute(
            "SELECT location, ts, score, detail FROM cortex_health "
            "WHERE location = ? ORDER BY ts DESC LIMIT 1",
            (location,),
        ).fetchone()
        if not row:
            return None
        return {
            "location": row["location"],
            "ts": row["ts"],
            "score": row["score"],
            "detail": json.loads(row["detail"]),
        }

    # ── Effect Profiles ────────────────────────────────────────────

    def upsert_effect(
        self,
        device_id: str,
        actuator: str,
        action: str,
        sensor: str,
        avg_delta_5m: float,
        std_dev: float,
        sample_count: int,
        sum_values: float,
        sum_squares: float,
    ) -> None:
        """Insert or replace an effect profile entry."""
        db = self._get_db()
        now = int(time.time() * 1000)
        db.execute(
            "INSERT OR REPLACE INTO cortex_effects "
            "(device_id, actuator, action, sensor, avg_delta_5m, std_dev, "
            "sample_count, sum_values, sum_squares, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (device_id, actuator, action, sensor, avg_delta_5m, std_dev,
             sample_count, sum_values, sum_squares, now),
        )
        db.commit()

    def get_effect(
        self,
        device_id: str,
        actuator: str,
        action: str,
        sensor: str,
    ) -> dict | None:
        """Get a single effect entry."""
        db = self._get_db()
        row = db.execute(
            "SELECT * FROM cortex_effects "
            "WHERE device_id = ? AND actuator = ? AND action = ? AND sensor = ?",
            (device_id, actuator, action, sensor),
        ).fetchone()
        if not row:
            return None
        return self._effect_row_to_dict(row)

    def get_effects(
        self,
        device_id: str | None = None,
        actuator: str | None = None,
        min_samples: int = 0,
    ) -> list[dict]:
        """Query effect profiles with optional filters."""
        db = self._get_db()
        sql = "SELECT * FROM cortex_effects WHERE 1=1"
        params: list[Any] = []

        if device_id:
            sql += " AND device_id = ?"
            params.append(device_id)
        if actuator:
            sql += " AND actuator = ?"
            params.append(actuator)
        if min_samples > 0:
            sql += " AND sample_count >= ?"
            params.append(min_samples)

        sql += " ORDER BY device_id, actuator, action, sensor"
        cursor = db.execute(sql, params)
        return [self._effect_row_to_dict(row) for row in cursor.fetchall()]

    def _effect_row_to_dict(self, row: sqlite3.Row) -> dict:
        return {
            "deviceId": row["device_id"],
            "actuator": row["actuator"],
            "action": row["action"],
            "sensor": row["sensor"],
            "avgDelta5m": row["avg_delta_5m"],
            "stdDev": row["std_dev"],
            "sampleCount": row["sample_count"],
            "confidence": min(1.0, row["sample_count"] / 10),
            "updatedAt": row["updated_at"],
        }

    # ── Location Goals ─────────────────────────────────────────────

    def upsert_location_goal(self, location: str, goal: str) -> dict:
        """Insert or replace a location goal."""
        db = self._get_db()
        now = int(time.time() * 1000)
        db.execute(
            "INSERT INTO location_goals (location, goal, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(location) DO UPDATE SET goal = excluded.goal, updated_at = excluded.updated_at",
            (location, goal, now, now),
        )
        db.commit()
        return {"location": location, "goal": goal, "createdAt": now, "updatedAt": now}

    def get_location_goal(self, location: str) -> dict | None:
        """Get the goal for a location."""
        db = self._get_db()
        row = db.execute(
            "SELECT * FROM location_goals WHERE location = ?", (location,)
        ).fetchone()
        if not row:
            return None
        return {
            "location": row["location"],
            "goal": row["goal"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def get_all_location_goals(self) -> list[dict]:
        """Get all location goals."""
        db = self._get_db()
        rows = db.execute("SELECT * FROM location_goals ORDER BY location").fetchall()
        return [
            {
                "location": row["location"],
                "goal": row["goal"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def delete_location_goal(self, location: str) -> bool:
        """Delete a location goal."""
        db = self._get_db()
        cursor = db.execute("DELETE FROM location_goals WHERE location = ?", (location,))
        db.commit()
        return cursor.rowcount > 0

    def _row_to_device(self, row: sqlite3.Row) -> Device:
        caps_raw = json.loads(row["capabilities"]) if row["capabilities"] else {"sensors": [], "actuators": []}
        actuator_names_raw = json.loads(row["actuator_names"]) if row["actuator_names"] else {}

        capabilities = DeviceCapabilities(
            sensors=[
                Sensor(id=s["id"], type=s["type"], name=s.get("name"), unit=s.get("unit"))
                for s in caps_raw.get("sensors", [])
            ],
            actuators=[
                Actuator(
                    id=a["id"], type=a["type"],
                    pin=a.get("pin"), name=a.get("name"), state=a.get("state"),
                )
                for a in caps_raw.get("actuators", [])
            ],
        )

        return Device(
            id=row["id"],
            location=row["location"],
            name=row["name"],
            platform=row["platform"],
            firmware=row["firmware"],
            capabilities=capabilities,
            actuator_names=actuator_names_raw,
            telemetry_interval_ms=row["telemetry_interval_ms"],
            online=bool(row["online"]),
            last_seen=row["last_seen"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            display_order=row["display_order"],
        )
