"""Sensor fusion and staleness detection.

Reads latest sensor values from Redis, detects staleness, and builds
measurement vectors for the EKF. Supports multiple sensor sources
(ESP32, AROYA) with inverse-variance weighting via the Kalman framework.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from ..mpc.estimator import NZ_MAX
from ..mpc.model import w_sat

logger = logging.getLogger(__name__)

# Staleness thresholds (seconds)
ESP32_STALE_THRESHOLD = 5.0     # ESP32 data > 5s old → increase EKF noise
AROYA_STALE_THRESHOLD = 600.0   # AROYA data > 10min old → mark unavailable


class SensorFusion:
    """Fuses sensor readings from multiple sources into EKF measurement vectors.

    Reads from Redis (populated by MQTT subscriber) and handles:
    - Multiple sensor sources with different update rates
    - Staleness detection with configurable thresholds
    - Graceful degradation when sensors go offline
    """

    def __init__(
        self,
        redis_client=None,
        location: str = "room1",
        device_id: str = "esp32-1",
    ):
        self.redis = redis_client
        self.location = location
        self.device_id = device_id

        # Last known good readings (fallback)
        self._last_readings: dict[str, float] = {}
        self._last_update_time: dict[str, float] = {}

        # Sensor ID → measurement mapping
        self.sensor_map: dict[str, str] = {
            "temp1": "T_air",
            "hum1": "RH",
            "co2_1": "CO2",
            "leaf_temp1": "T_leaf",
            "soil1": "theta",
            "supply_temp1": "T_supply",
        }

    async def read_sensors(self) -> tuple[np.ndarray, np.ndarray, bool]:
        """Read latest sensor values and build measurement vector.

        Returns:
            z_meas: Measurement vector (6,) with NaN for missing
            available: Boolean mask (6,) of available sensors
            is_stale: True if primary sensor data is stale
        """
        now = time.time()
        readings: dict[str, float] = {}
        is_stale = False

        if self.redis is not None:
            try:
                readings = await self._read_from_redis()
            except Exception as e:
                logger.warning(f"Redis read failed: {e}")

        # Fallback to last known if Redis fails
        if not readings:
            readings = dict(self._last_readings)
            is_stale = True
        else:
            self._last_readings = dict(readings)
            self._last_update_time["esp32"] = now

        # Check staleness
        esp32_age = now - self._last_update_time.get("esp32", 0)
        if esp32_age > ESP32_STALE_THRESHOLD:
            is_stale = True
            logger.debug(f"ESP32 data stale ({esp32_age:.1f}s old)")

        # Build measurement vector
        z_meas, available = self._build_measurement(readings)

        return z_meas, available, is_stale

    def read_sensors_sync(
        self,
        readings: dict[str, float],
    ) -> tuple[np.ndarray, np.ndarray, bool]:
        """Synchronous version — build measurement from provided readings.

        For use when readings come from MQTT callback (not Redis).
        """
        now = time.time()
        self._last_readings = dict(readings)
        self._last_update_time["esp32"] = now

        z_meas, available = self._build_measurement(readings)
        return z_meas, available, False

    def _build_measurement(
        self,
        readings: dict[str, float],
    ) -> tuple[np.ndarray, np.ndarray]:
        """Convert readings dict to measurement vector."""
        z_meas = np.full(NZ_MAX, np.nan)

        for sensor_id, value in readings.items():
            meas_name = self.sensor_map.get(sensor_id)
            if meas_name is None:
                continue

            if meas_name == "T_air":
                z_meas[0] = value
            elif meas_name == "RH":
                # Convert RH → absolute humidity (g/kg)
                t_air = readings.get("temp1", 25.0)
                ws = w_sat(t_air)
                z_meas[1] = ws * value / 100.0
            elif meas_name == "CO2":
                z_meas[2] = value
            elif meas_name == "T_leaf":
                z_meas[3] = value
            elif meas_name == "theta":
                z_meas[4] = value
            elif meas_name == "T_supply":
                z_meas[5] = value

        available = ~np.isnan(z_meas)
        return z_meas, available

    async def _read_from_redis(self) -> dict[str, float]:
        """Read latest sensor values from Redis hash."""
        if self.redis is None:
            return {}

        key = f"sensors:esp32:{self.location}:latest"
        try:
            # Try async Redis
            if hasattr(self.redis, 'hgetall'):
                data = await self.redis.hgetall(key)
            else:
                data = {}
        except Exception:
            data = {}

        readings = {}
        if data:
            for k, v in data.items():
                try:
                    key_str = k.decode() if isinstance(k, bytes) else k
                    val_str = v.decode() if isinstance(v, bytes) else v
                    readings[key_str] = float(val_str)
                except (ValueError, AttributeError):
                    pass

        return readings
