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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Types

@dataclass
class TelemetryRow:
    ts: int
    temp: float
    humidity: float
    source_topic: str | None = None
    device_id: str | None = None


@dataclass
class TelemetryBucketRow:
    ts: int
    temp: float
    humidity: float
    count: int
    device_id: str | None = None


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
                    {"id": s.id, "type": s.type, **({"name": s.name} if s.name else {})}
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
        db.commit()

    def _get_db(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None

    # ── Sensor Readings ──────────────────────────────────────────────

    def insert_reading(self, row: TelemetryRow) -> None:
        db = self._get_db()
        db.execute(
            "INSERT INTO sensor_readings (ts, temp, humidity, source_topic, device_id) VALUES (?, ?, ?, ?, ?)",
            (row.ts, row.temp, row.humidity, row.source_topic, row.device_id),
        )
        db.commit()

    def query_history_raw(
        self,
        since_ms: int,
        until_ms: int,
        limit: int = 5000,
        device_id: str | None = None,
    ) -> list[TelemetryRow]:
        db = self._get_db()
        if device_id:
            cursor = db.execute(
                "SELECT ts, temp, humidity, source_topic, device_id FROM sensor_readings "
                "WHERE ts >= ? AND ts <= ? AND device_id = ? ORDER BY ts ASC LIMIT ?",
                (since_ms, until_ms, device_id, limit),
            )
        else:
            cursor = db.execute(
                "SELECT ts, temp, humidity, source_topic, device_id FROM sensor_readings "
                "WHERE ts >= ? AND ts <= ? ORDER BY ts ASC LIMIT ?",
                (since_ms, until_ms, limit),
            )
        return [
            TelemetryRow(
                ts=row["ts"],
                temp=row["temp"],
                humidity=row["humidity"],
                source_topic=row["source_topic"],
                device_id=row["device_id"],
            )
            for row in cursor.fetchall()
        ]

    def query_history_bucketed(
        self,
        since_ms: int,
        until_ms: int,
        bucket_ms: int,
        limit: int = 5000,
        device_id: str | None = None,
    ) -> list[TelemetryBucketRow]:
        db = self._get_db()
        if device_id:
            cursor = db.execute(
                """
                SELECT
                    (CAST(ts / ? AS INTEGER) * ?) AS ts,
                    AVG(temp) AS temp,
                    AVG(humidity) AS humidity,
                    COUNT(1) AS count,
                    device_id
                FROM sensor_readings
                WHERE ts >= ? AND ts <= ? AND device_id = ?
                GROUP BY (CAST(ts / ? AS INTEGER) * ?)
                ORDER BY ts ASC
                LIMIT ?
                """,
                (bucket_ms, bucket_ms, since_ms, until_ms, device_id, bucket_ms, bucket_ms, limit),
            )
        else:
            cursor = db.execute(
                """
                SELECT
                    (CAST(ts / ? AS INTEGER) * ?) AS ts,
                    AVG(temp) AS temp,
                    AVG(humidity) AS humidity,
                    COUNT(1) AS count,
                    device_id
                FROM sensor_readings
                WHERE ts >= ? AND ts <= ?
                GROUP BY (CAST(ts / ? AS INTEGER) * ?)
                ORDER BY ts ASC
                LIMIT ?
                """,
                (bucket_ms, bucket_ms, since_ms, until_ms, bucket_ms, bucket_ms, limit),
            )
        return [
            TelemetryBucketRow(
                ts=row["ts"],
                temp=row["temp"],
                humidity=row["humidity"],
                count=row["count"],
                device_id=row["device_id"],
            )
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
            "sensors": [{"id": s.id, "type": s.type, **({"name": s.name} if s.name else {})} for s in device.capabilities.sensors],
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

    def set_device_online(self, device_id: str) -> bool:
        db = self._get_db()
        now = int(time.time() * 1000)
        cursor = db.execute(
            "UPDATE devices SET online = 1, last_seen = ?, updated_at = ? WHERE id = ?",
            (now, now, device_id),
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

    # ── Suggestions (Rule Advisor) ──────────────────────────────────

    def insert_suggestion(
        self,
        id: str,
        rule_name: str,
        field: str,
        current_value: str,
        suggested_value: str,
        reason: str,
        confidence: float,
        outcome_sample_count: int = 0,
        observation_context: str | None = None,
    ) -> dict:
        db = self._get_db()
        now = int(time.time() * 1000)
        db.execute(
            "INSERT INTO cortex_suggestions "
            "(id, created_at, rule_name, field, current_value, suggested_value, "
            "reason, confidence, status, outcome_sample_count, observation_context) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (id, now, rule_name, field, current_value, suggested_value,
             reason, confidence, outcome_sample_count, observation_context),
        )
        db.commit()
        return self._suggestion_to_dict(db.execute(
            "SELECT * FROM cortex_suggestions WHERE id = ?", (id,)
        ).fetchone())

    def get_suggestions(
        self,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        db = self._get_db()
        if status:
            cursor = db.execute(
                "SELECT * FROM cortex_suggestions WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = db.execute(
                "SELECT * FROM cortex_suggestions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [self._suggestion_to_dict(row) for row in cursor.fetchall()]

    def update_suggestion_status(self, id: str, status: str) -> bool:
        db = self._get_db()
        now = int(time.time() * 1000)
        cursor = db.execute(
            "UPDATE cortex_suggestions SET status = ?, resolved_at = ? WHERE id = ?",
            (status, now, id),
        )
        db.commit()
        return cursor.rowcount > 0

    def get_suggestion(self, id: str) -> dict | None:
        db = self._get_db()
        row = db.execute(
            "SELECT * FROM cortex_suggestions WHERE id = ?", (id,)
        ).fetchone()
        if not row:
            return None
        return self._suggestion_to_dict(row)

    def _suggestion_to_dict(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "createdAt": row["created_at"],
            "ruleName": row["rule_name"],
            "field": row["field"],
            "currentValue": row["current_value"],
            "suggestedValue": row["suggested_value"],
            "reason": row["reason"],
            "confidence": row["confidence"],
            "status": row["status"],
            "resolvedAt": row["resolved_at"],
            "outcomeSampleCount": row["outcome_sample_count"],
            "observationContext": row["observation_context"],
        }

    def count_suggestions_by_status(self) -> dict[str, int]:
        db = self._get_db()
        cursor = db.execute(
            "SELECT status, COUNT(*) as cnt FROM cortex_suggestions GROUP BY status"
        )
        counts: dict[str, int] = {}
        for row in cursor.fetchall():
            counts[row["status"]] = row["cnt"]
        return counts

    def _row_to_device(self, row: sqlite3.Row) -> Device:
        caps_raw = json.loads(row["capabilities"]) if row["capabilities"] else {"sensors": [], "actuators": []}
        actuator_names_raw = json.loads(row["actuator_names"]) if row["actuator_names"] else {}

        capabilities = DeviceCapabilities(
            sensors=[
                Sensor(id=s["id"], type=s["type"], name=s.get("name"))
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
