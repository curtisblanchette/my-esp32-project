"""
Cortex memory — persistent baselines for learned sensor patterns.

Stores per-hour-of-day baselines so the decision engine and LLM
can compare current readings against "what is normal for this time."
Uses Welford's online algorithm for incremental mean/variance updates.
"""

import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sqlite_client import SqliteClient

logger = logging.getLogger(__name__)


@dataclass
class Baseline:
    device_id: str
    sensor: str       # "temperature" | "humidity"
    hour_of_day: int  # 0-23
    avg_value: float
    std_dev: float
    sample_count: int


class CortexMemory:
    """Manages persistent baselines in the existing SQLite database."""

    def __init__(self, sqlite: "SqliteClient"):
        self._sqlite = sqlite
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Create baselines table if it doesn't exist."""
        db = self._sqlite._get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS cortex_baselines (
                device_id TEXT NOT NULL,
                sensor TEXT NOT NULL,
                hour_of_day INTEGER NOT NULL,
                avg_value REAL NOT NULL DEFAULT 0,
                std_dev REAL NOT NULL DEFAULT 0,
                sample_count INTEGER NOT NULL DEFAULT 0,
                sum_values REAL NOT NULL DEFAULT 0,
                sum_squares REAL NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (device_id, sensor, hour_of_day)
            );
        """)
        db.commit()

    def update_baseline(
        self,
        device_id: str,
        sensor: str,
        hour_of_day: int,
        value: float,
    ) -> None:
        """Incrementally update the baseline for a device/sensor/hour."""
        db = self._sqlite._get_db()
        now = int(time.time() * 1000)

        row = db.execute(
            "SELECT sample_count, sum_values, sum_squares FROM cortex_baselines "
            "WHERE device_id = ? AND sensor = ? AND hour_of_day = ?",
            (device_id, sensor, hour_of_day),
        ).fetchone()

        if row:
            count = row["sample_count"] + 1
            sum_v = row["sum_values"] + value
            sum_sq = row["sum_squares"] + value * value
        else:
            count = 1
            sum_v = value
            sum_sq = value * value

        avg = sum_v / count
        variance = (sum_sq / count) - (avg * avg) if count > 1 else 0.0
        std_dev = math.sqrt(max(0, variance))

        db.execute(
            """
            INSERT INTO cortex_baselines
                (device_id, sensor, hour_of_day, avg_value, std_dev, sample_count,
                 sum_values, sum_squares, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (device_id, sensor, hour_of_day) DO UPDATE SET
                avg_value = ?,
                std_dev = ?,
                sample_count = ?,
                sum_values = ?,
                sum_squares = ?,
                updated_at = ?
            """,
            (device_id, sensor, hour_of_day, avg, std_dev, count, sum_v, sum_sq, now,
             avg, std_dev, count, sum_v, sum_sq, now),
        )
        db.commit()

    def get_baseline(
        self,
        device_id: str,
        sensor: str,
        hour_of_day: int,
    ) -> Baseline | None:
        """Get the baseline for a specific device/sensor/hour."""
        db = self._sqlite._get_db()
        row = db.execute(
            "SELECT * FROM cortex_baselines WHERE device_id = ? AND sensor = ? AND hour_of_day = ?",
            (device_id, sensor, hour_of_day),
        ).fetchone()

        if not row:
            return None

        return Baseline(
            device_id=row["device_id"],
            sensor=row["sensor"],
            hour_of_day=row["hour_of_day"],
            avg_value=row["avg_value"],
            std_dev=row["std_dev"],
            sample_count=row["sample_count"],
        )

    def get_baseline_deviation(
        self,
        device_id: str,
        sensor: str,
        hour_of_day: int,
        current_value: float,
    ) -> float | None:
        """
        How many standard deviations is current_value from the baseline?
        Returns None if no baseline exists yet (< 10 samples).
        """
        baseline = self.get_baseline(device_id, sensor, hour_of_day)
        if not baseline or baseline.sample_count < 10:
            return None
        if baseline.std_dev == 0:
            return 0.0
        return (current_value - baseline.avg_value) / baseline.std_dev
