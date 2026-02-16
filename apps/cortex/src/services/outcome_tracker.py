"""
Outcome Tracker — correlates commands with their measurable sensor effects.

Phase 2 of the cybernetic loop. After a command fires (e.g., relay1 ON because
temp > 25), the tracker snapshots the current sensor state, watches for changes
at 1m/5m/10m intervals, and scores effectiveness from -1.0 to +1.0.

Lifecycle: track_command → handle_ack → check_outcomes (periodic) → score → store
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .data_reader import DataReader
    from .sqlite_client import SqliteClient

from ..models.command import Command, CommandAck
from ..models.telemetry import TelemetryMessage

logger = logging.getLogger(__name__)

# Scale factors for normalizing score across different sensor types
SCALE_FACTORS = {
    "temperature": 2.0,      # 2°C change = full score
    "humidity": 5.0,          # 5% change = full score
    "soil_moisture": 5.0,     # 5% change = full score
    "light_level": 100.0,     # 100 lux change = full score
    "co2": 100.0,             # 100 ppm change = full score
    "pressure": 5.0,          # 5 hPa change = full score
}

# Check interval weights (must sum to 1.0)
INTERVAL_WEIGHTS = {
    60: 0.20,   # 1 minute: early signal
    300: 0.50,  # 5 minutes: primary signal
    600: 0.30,  # 10 minutes: final confirmation
}

# Keywords for inferring target metric from reason string
TEMP_KEYWORDS = {"temp", "temperature", "hot", "cold", "cool", "heat", "warm", "thermal"}
HUMIDITY_KEYWORDS = {"hum", "humidity", "moist", "dry", "damp", "wet"}
SOIL_KEYWORDS = {"soil", "moisture", "irrigation", "water", "plant"}
LIGHT_KEYWORDS = {"light", "lux", "bright", "dark", "dim", "illuminate"}
CO2_KEYWORDS = {"co2", "carbon", "air quality", "ventilat"}
PRESSURE_KEYWORDS = {"pressure", "baro", "altitude"}

METRIC_KEYWORD_MAP = [
    ("soil_moisture", SOIL_KEYWORDS),
    ("light_level", LIGHT_KEYWORDS),
    ("humidity", HUMIDITY_KEYWORDS),
    ("co2", CO2_KEYWORDS),
    ("pressure", PRESSURE_KEYWORDS),
    ("temperature", TEMP_KEYWORDS),
]

# Keywords for inferring desired direction
DECREASE_KEYWORDS = {"cool", "lower", "drop", "reduce", "decrease", "save energy", "too hot", "overheating",
                     "dehumidif", "exhaust"}
INCREASE_KEYWORDS = {"heat", "warm", "raise", "increase", "too cold", "freezing",
                     "humidif", "lights on", "grow light", "illuminate"}

# What direction does "turning ON" push each metric?
# ON pushes the metric in this direction; OFF pushes the opposite.
ON_DIRECTION_BY_METRIC: dict[str, str] = {
    "temperature": "decrease",    # fan/exhaust ON → cools temp
    "humidity": "decrease",       # dehumidifier ON → decreases humidity
    "soil_moisture": "increase",  # irrigation ON → increases soil moisture
    "light_level": "increase",    # grow light ON → increases light
    "co2": "decrease",            # ventilation ON → decreases CO2
    "pressure": "decrease",       # vent ON → decreases pressure
}


@dataclass
class SensorSnapshot:
    ts: int
    values: dict[str, float]  # {"temp1": 26.3, "hum1": 58.2}


@dataclass
class PendingOutcome:
    correlation_id: str
    device_id: str
    target: str
    action: str
    value: Any
    reason: str | None
    command_ts: int
    pre_snapshot: SensorSnapshot
    target_metric: str       # "temperature" or "humidity"
    desired_direction: str   # "decrease" or "increase"
    acked: bool = False
    ack_status: str | None = None
    check_intervals: list[int] = field(default_factory=lambda: [60, 300, 600])
    post_snapshots: dict[int, SensorSnapshot] = field(default_factory=dict)
    next_check_idx: int = 0


@dataclass
class OutcomeRecord:
    correlation_id: str
    device_id: str
    target: str
    action: str
    value: Any
    reason: str | None
    command_ts: int
    ack_status: str
    target_metric: str
    desired_direction: str
    pre_value: float
    post_1m: float | None
    post_5m: float | None
    post_10m: float | None
    effectiveness: float
    scored_at: int


class OutcomeTracker:
    """Tracks command outcomes by correlating commands with sensor changes."""

    def __init__(self, sqlite: "SqliteClient", data_reader: "DataReader"):
        self._sqlite = sqlite
        self._data_reader = data_reader
        self._pending: dict[str, PendingOutcome] = {}
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Create outcome tracking table if it doesn't exist."""
        db = self._sqlite._get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS cortex_outcomes (
                correlation_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                target TEXT NOT NULL,
                action TEXT NOT NULL,
                value JSON,
                reason TEXT,
                command_ts INTEGER NOT NULL,
                ack_status TEXT NOT NULL,
                target_metric TEXT NOT NULL,
                desired_direction TEXT NOT NULL,
                pre_value REAL NOT NULL,
                post_1m REAL,
                post_5m REAL,
                post_10m REAL,
                effectiveness REAL NOT NULL,
                scored_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_outcomes_device_target
                ON cortex_outcomes(device_id, target);
            CREATE INDEX IF NOT EXISTS idx_outcomes_ts
                ON cortex_outcomes(command_ts);
        """)
        db.commit()

    def track_command(self, command: Command, telemetry: TelemetryMessage) -> None:
        """Start tracking a command's outcome by snapshotting current sensor state."""
        if not command.correlation_id:
            return

        # Build pre-snapshot from current telemetry
        values: dict[str, float] = {}
        for reading in telemetry.readings:
            if isinstance(reading.value, (int, float)):
                values[reading.id] = float(reading.value)

        if not values:
            logger.warning(f"No numeric readings to snapshot for {command.correlation_id}")
            return

        target_metric = self._infer_target_metric(command.reason)
        desired_direction = self._infer_desired_direction(
            command.reason, command.value, target_metric,
        )

        pending = PendingOutcome(
            correlation_id=command.correlation_id,
            device_id=command.device_id,
            target=command.target,
            action=command.action,
            value=command.value,
            reason=command.reason,
            command_ts=int(time.time() * 1000),
            pre_snapshot=SensorSnapshot(ts=telemetry.ts, values=values),
            target_metric=target_metric,
            desired_direction=desired_direction,
        )

        self._pending[command.correlation_id] = pending
        logger.info(
            f"Tracking outcome for {command.correlation_id}: "
            f"target_metric={target_metric}, desired={desired_direction}, "
            f"pre={values}"
        )

    def handle_ack(self, ack: CommandAck) -> None:
        """Handle a command acknowledgment."""
        pending = self._pending.get(ack.correlation_id)
        if not pending:
            return

        pending.acked = True
        pending.ack_status = ack.status

        if ack.status != "executed":
            # Command failed — score as 0.0 and store immediately
            logger.info(f"Command {ack.correlation_id} not executed ({ack.status}), scoring as 0.0")
            self._complete_outcome(pending, effectiveness=0.0)

    def check_outcomes(self) -> list[OutcomeRecord]:
        """Check pending outcomes and collect post-snapshots at intervals."""
        now = time.time()
        completed: list[OutcomeRecord] = []
        to_remove: list[str] = []

        for cid, pending in self._pending.items():
            if pending.next_check_idx >= len(pending.check_intervals):
                continue

            command_time = pending.command_ts / 1000.0  # ms → seconds
            next_interval = pending.check_intervals[pending.next_check_idx]
            check_time = command_time + next_interval

            if now < check_time:
                continue

            # Time to collect a post-snapshot
            sensor_id = self._resolve_sensor_id(pending)
            current_value = self._get_current_value(pending.device_id, pending.target_metric)

            if current_value is not None:
                pending.post_snapshots[next_interval] = SensorSnapshot(
                    ts=int(now * 1000),
                    values={sensor_id: current_value},
                )
                logger.debug(
                    f"Outcome {cid}: {next_interval}s snapshot "
                    f"{sensor_id}={current_value:.1f}"
                )

            pending.next_check_idx += 1

            # All intervals checked — score and store
            if pending.next_check_idx >= len(pending.check_intervals):
                record = self._finalize_outcome(pending)
                if record:
                    completed.append(record)
                to_remove.append(cid)

        for cid in to_remove:
            del self._pending[cid]

        return completed

    def get_effectiveness_summary(
        self,
        device_id: str,
        target: str,
        limit: int = 20,
    ) -> dict | None:
        """Get aggregated effectiveness data for a device/target pair."""
        db = self._sqlite._get_db()
        cursor = db.execute(
            "SELECT effectiveness, pre_value, post_5m, target_metric "
            "FROM cortex_outcomes "
            "WHERE device_id = ? AND target = ? "
            "ORDER BY command_ts DESC LIMIT ?",
            (device_id, target, limit),
        )
        rows = cursor.fetchall()

        if not rows:
            return None

        scores = [row["effectiveness"] for row in rows]
        avg_score = sum(scores) / len(scores)
        success_count = sum(1 for s in scores if s > 0.1)

        # Calculate average delta at 5 minutes
        deltas = []
        for row in rows:
            if row["post_5m"] is not None:
                deltas.append(row["post_5m"] - row["pre_value"])

        avg_delta_5m = sum(deltas) / len(deltas) if deltas else 0.0
        metric = rows[0]["target_metric"] if rows else "temperature"

        return {
            "avg_score": round(avg_score, 3),
            "sample_count": len(rows),
            "avg_delta_5m": round(avg_delta_5m, 2),
            "success_rate": round(success_count / len(rows), 3),
            "metric": metric,
        }

    def query_outcomes(
        self,
        device_id: str | None = None,
        target: str | None = None,
        since_ms: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query stored outcomes for the API endpoint."""
        db = self._sqlite._get_db()

        sql = ("SELECT correlation_id, device_id, target, action, value, reason, "
               "command_ts, ack_status, target_metric, desired_direction, "
               "pre_value, post_1m, post_5m, post_10m, effectiveness, scored_at "
               "FROM cortex_outcomes WHERE 1=1")
        params: list[Any] = []

        if device_id:
            sql += " AND device_id = ?"
            params.append(device_id)
        if target:
            sql += " AND target = ?"
            params.append(target)
        if since_ms:
            sql += " AND command_ts >= ?"
            params.append(since_ms)

        sql += " ORDER BY command_ts DESC LIMIT ?"
        params.append(limit)

        cursor = db.execute(sql, params)
        return [
            {
                "correlationId": row["correlation_id"],
                "deviceId": row["device_id"],
                "target": row["target"],
                "action": row["action"],
                "value": json.loads(row["value"]) if row["value"] else None,
                "reason": row["reason"],
                "commandTs": row["command_ts"],
                "ackStatus": row["ack_status"],
                "targetMetric": row["target_metric"],
                "desiredDirection": row["desired_direction"],
                "preValue": row["pre_value"],
                "post1m": row["post_1m"],
                "post5m": row["post_5m"],
                "post10m": row["post_10m"],
                "effectiveness": row["effectiveness"],
                "scoredAt": row["scored_at"],
            }
            for row in cursor.fetchall()
        ]

    # ── Internal Helpers ──────────────────────────────────────────────

    def _get_current_value(self, device_id: str, metric: str) -> float | None:
        """Read current sensor value via DataReader."""
        try:
            readings = self._data_reader.get_recent_readings(
                window_minutes=2, device_id=device_id,
            )
            if not readings:
                return None

            latest = readings[-1]
            # Find sensor matching the metric type
            from .sensor_meta import guess_sensor_type
            for sensor_id, value in latest.readings.items():
                if guess_sensor_type(sensor_id) == metric:
                    return value
            return None
        except Exception as e:
            logger.error(f"Failed to read current value for {device_id}/{metric}: {e}")
            return None

    def _resolve_sensor_id(self, pending: PendingOutcome) -> str:
        """Resolve the sensor_id from pre_snapshot that matches the target metric."""
        from .sensor_meta import guess_sensor_type
        for sid in pending.pre_snapshot.values:
            if guess_sensor_type(sid) == pending.target_metric:
                return sid
        # Fallback: return first sensor_id
        return next(iter(pending.pre_snapshot.values), "temp1")

    def _finalize_outcome(self, pending: PendingOutcome) -> OutcomeRecord | None:
        """Score a completed outcome and store to SQLite."""
        sensor_id = self._resolve_sensor_id(pending)
        pre_value = pending.pre_snapshot.values.get(sensor_id)
        if pre_value is None:
            logger.warning(f"No pre-value for {pending.correlation_id}, skipping")
            return None

        # Extract post-values for each interval
        post_values: dict[int, float] = {}
        for interval, snapshot in pending.post_snapshots.items():
            val = snapshot.values.get(sensor_id)
            if val is not None:
                post_values[interval] = val

        scale = SCALE_FACTORS.get(pending.target_metric, 2.0)
        effectiveness = self._score_outcome(
            pre_value, post_values, pending.desired_direction, scale,
        )

        record = OutcomeRecord(
            correlation_id=pending.correlation_id,
            device_id=pending.device_id,
            target=pending.target,
            action=pending.action,
            value=pending.value,
            reason=pending.reason,
            command_ts=pending.command_ts,
            ack_status=pending.ack_status or "unknown",
            target_metric=pending.target_metric,
            desired_direction=pending.desired_direction,
            pre_value=pre_value,
            post_1m=post_values.get(60),
            post_5m=post_values.get(300),
            post_10m=post_values.get(600),
            effectiveness=effectiveness,
            scored_at=int(time.time() * 1000),
        )

        self._store_outcome(record)
        logger.info(
            f"Outcome {pending.correlation_id}: "
            f"effectiveness={effectiveness:+.2f} "
            f"(pre={pre_value:.1f}, post_5m={post_values.get(300, 'N/A')})"
        )
        return record

    def _complete_outcome(self, pending: PendingOutcome, effectiveness: float) -> None:
        """Store a failed/immediate outcome and remove from pending."""
        sensor_id = self._resolve_sensor_id(pending)
        pre_value = pending.pre_snapshot.values.get(sensor_id, 0.0)

        record = OutcomeRecord(
            correlation_id=pending.correlation_id,
            device_id=pending.device_id,
            target=pending.target,
            action=pending.action,
            value=pending.value,
            reason=pending.reason,
            command_ts=pending.command_ts,
            ack_status=pending.ack_status or "failed",
            target_metric=pending.target_metric,
            desired_direction=pending.desired_direction,
            pre_value=pre_value,
            post_1m=None,
            post_5m=None,
            post_10m=None,
            effectiveness=effectiveness,
            scored_at=int(time.time() * 1000),
        )

        self._store_outcome(record)
        self._pending.pop(pending.correlation_id, None)

    def _store_outcome(self, record: OutcomeRecord) -> None:
        """Persist an outcome record to SQLite."""
        db = self._sqlite._get_db()
        db.execute(
            "INSERT OR REPLACE INTO cortex_outcomes "
            "(correlation_id, device_id, target, action, value, reason, "
            "command_ts, ack_status, target_metric, desired_direction, "
            "pre_value, post_1m, post_5m, post_10m, effectiveness, scored_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.correlation_id,
                record.device_id,
                record.target,
                record.action,
                json.dumps(record.value) if record.value is not None else None,
                record.reason,
                record.command_ts,
                record.ack_status,
                record.target_metric,
                record.desired_direction,
                record.pre_value,
                record.post_1m,
                record.post_5m,
                record.post_10m,
                record.effectiveness,
                record.scored_at,
            ),
        )
        db.commit()

    @staticmethod
    def _infer_target_metric(reason: str | None) -> str:
        """Infer which sensor metric a command targets from its reason string."""
        if not reason:
            return "temperature"

        reason_lower = reason.lower()
        for metric, keywords in METRIC_KEYWORD_MAP:
            for keyword in keywords:
                if keyword in reason_lower:
                    return metric

        return "temperature"

    @staticmethod
    def _infer_desired_direction(
        reason: str | None, value: Any, target_metric: str | None = None,
    ) -> str:
        """Infer the desired sensor direction from reason, value, and metric.

        Priority:
        1. Explicit keywords in the reason string
        2. Metric-aware ON/OFF inference (e.g., irrigation ON → soil_moisture increase)
        3. Legacy fallback: ON → decrease (assumes cooling)
        """
        if reason:
            reason_lower = reason.lower()
            for keyword in DECREASE_KEYWORDS:
                if keyword in reason_lower:
                    return "decrease"
            for keyword in INCREASE_KEYWORDS:
                if keyword in reason_lower:
                    return "increase"

        # Metric-aware fallback: what direction does ON/OFF push this metric?
        if target_metric and target_metric in ON_DIRECTION_BY_METRIC:
            on_direction = ON_DIRECTION_BY_METRIC[target_metric]
            if value is True:
                return on_direction
            else:
                return "increase" if on_direction == "decrease" else "decrease"

        # Legacy fallback for unknown metrics
        if value is True:
            return "decrease"
        return "increase"

    @staticmethod
    def _score_outcome(
        pre_value: float,
        post_snapshots: dict[int, float],
        desired_direction: str,
        scale_factor: float,
    ) -> float:
        """
        Score effectiveness from -1.0 to +1.0.

        Weighted across intervals: 1m=20%, 5m=50%, 10m=30%.
        Normalized by scale_factor so different sensor types are comparable.
        """
        if not post_snapshots:
            return 0.0

        weighted_score = 0.0
        total_weight = 0.0

        for interval, weight in INTERVAL_WEIGHTS.items():
            post_value = post_snapshots.get(interval)
            if post_value is None:
                continue

            delta = post_value - pre_value

            if desired_direction == "decrease":
                interval_score = -delta / scale_factor
            else:
                interval_score = delta / scale_factor

            weighted_score += interval_score * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0

        # Normalize by actual weight used (handles missing intervals)
        score = weighted_score / total_weight

        # Clamp to [-1.0, 1.0]
        return max(-1.0, min(1.0, score))
