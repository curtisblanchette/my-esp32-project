"""Fail-safe graceful degradation logic.

Priority levels:
1. MPC running normally — dynamic setpoints, full optimization
2. MPC solve fails — hold last valid control action (up to 30s)
3. MPC offline — ESP32 firmware PID on last-known setpoints
4. ESP32 offline — TrolMaster or HVAC internal controller
5. Total control loss — hardware safety limits

Key principle: each layer is independently functional.
"""

from __future__ import annotations

import logging
import time
from enum import IntEnum

import numpy as np

logger = logging.getLogger(__name__)


class ControlPriority(IntEnum):
    """Control system priority levels (lower = better)."""
    MPC_NORMAL = 1
    MPC_HOLD = 2
    MPC_OFFLINE = 3
    DEVICE_PID = 4
    HARDWARE_SAFETY = 5


# Maximum consecutive MPC failures before falling to HOLD
MAX_CONSECUTIVE_FAILURES = 30

# Maximum time to hold last control action (seconds)
MAX_HOLD_TIME = 30.0

# Timeout before declaring MPC offline (seconds)
MPC_OFFLINE_TIMEOUT = 60.0


class FailsafeManager:
    """Manages graceful degradation between control priority levels.

    Tracks MPC solve success/failure, manages priority transitions,
    and provides safe fallback control actions.
    """

    def __init__(self):
        self._priority = ControlPriority.MPC_NORMAL
        self._consecutive_failures = 0
        self._last_success_time = time.time()
        self._last_valid_u: np.ndarray | None = None
        self._hold_start_time: float | None = None

        # Metrics
        self._total_solves = 0
        self._total_failures = 0

    @property
    def priority(self) -> ControlPriority:
        return self._priority

    @property
    def is_healthy(self) -> bool:
        return self._priority == ControlPriority.MPC_NORMAL

    def on_solve_success(self, u_optimal: np.ndarray) -> np.ndarray:
        """Called when MPC solve succeeds.

        Args:
            u_optimal: Optimal control action from MPC

        Returns:
            Control action to apply (same as input when healthy)
        """
        self._consecutive_failures = 0
        self._last_success_time = time.time()
        self._last_valid_u = u_optimal.copy()
        self._hold_start_time = None
        self._total_solves += 1

        if self._priority != ControlPriority.MPC_NORMAL:
            logger.info(f"MPC recovered to NORMAL from priority {self._priority.name}")
            self._priority = ControlPriority.MPC_NORMAL

        return u_optimal

    def on_solve_failure(self, status: str = "failed") -> np.ndarray:
        """Called when MPC solve fails.

        Returns:
            Safe fallback control action
        """
        self._consecutive_failures += 1
        self._total_solves += 1
        self._total_failures += 1

        logger.warning(
            f"MPC solve failed ({status}), "
            f"consecutive={self._consecutive_failures}"
        )

        # Transition to HOLD on first failure
        if self._priority == ControlPriority.MPC_NORMAL:
            self._priority = ControlPriority.MPC_HOLD
            self._hold_start_time = time.time()
            logger.warning("Transitioning to MPC_HOLD (holding last valid u)")

        # Check if we should escalate to OFFLINE
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._priority = ControlPriority.MPC_OFFLINE
            logger.error(
                f"MPC OFFLINE after {MAX_CONSECUTIVE_FAILURES} consecutive failures"
            )

        # Check hold timeout
        if self._hold_start_time is not None:
            hold_duration = time.time() - self._hold_start_time
            if hold_duration > MAX_HOLD_TIME:
                self._priority = ControlPriority.MPC_OFFLINE
                logger.error(f"MPC OFFLINE after {hold_duration:.1f}s hold timeout")

        return self._get_safe_action()

    def check_timeout(self) -> None:
        """Periodic check for MPC timeout (call from control loop)."""
        elapsed = time.time() - self._last_success_time
        if elapsed > MPC_OFFLINE_TIMEOUT and self._priority < ControlPriority.MPC_OFFLINE:
            self._priority = ControlPriority.MPC_OFFLINE
            logger.error(f"MPC OFFLINE: no successful solve in {elapsed:.1f}s")

    def _get_safe_action(self) -> np.ndarray:
        """Return a safe control action based on current priority."""
        if self._last_valid_u is not None and self._priority <= ControlPriority.MPC_HOLD:
            return self._last_valid_u.copy()

        # Conservative safe defaults (fan on, everything else off)
        return np.array([
            0.0,    # u_cool: off
            0.0,    # u_heat: off
            0.0,    # u_dehum: off
            0.0,    # u_hum: off
            0.0,    # u_co2: off (normally-closed solenoid = safe)
            0.3,    # u_fan: 30% (maintain airflow)
            0.0,    # u_irr: off (normally-closed solenoid = safe)
        ])

    def get_status(self) -> dict:
        """Return current failsafe status for monitoring."""
        return {
            "priority": self._priority.name,
            "priority_level": int(self._priority),
            "consecutive_failures": self._consecutive_failures,
            "last_success_age_s": round(time.time() - self._last_success_time, 1),
            "total_solves": self._total_solves,
            "total_failures": self._total_failures,
            "success_rate": round(
                (self._total_solves - self._total_failures) / max(self._total_solves, 1) * 100, 1
            ),
            "is_healthy": self.is_healthy,
        }

    def reset(self) -> None:
        """Reset failsafe state (e.g., after manual intervention)."""
        self._priority = ControlPriority.MPC_NORMAL
        self._consecutive_failures = 0
        self._last_success_time = time.time()
        self._hold_start_time = None
