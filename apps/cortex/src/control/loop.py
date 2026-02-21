"""Main 1 Hz control loop orchestrating MPC, EKF, and actuators.

All timing-critical operations happen here. Non-critical tasks (logging,
LLM reasoning, dashboard push) are dispatched as async tasks.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time

import numpy as np

from ..mpc.model import GrowRoomModel, RoomParams
from ..mpc.solver import MPCSolver, MPCSolveResult, MPCConfig
from ..mpc.estimator import StateEstimator
from ..mpc.reference import ReferenceGenerator, CostWeights
from ..mpc.compliance import ComplianceTracker
from .sensors import SensorFusion
from .actuators import ActuatorInterface
from .failsafe import FailsafeManager, ControlPriority

logger = logging.getLogger(__name__)


class ControlLoop:
    """Main 1 Hz control loop. All timing-critical operations happen here.

    Non-critical tasks (logging, compliance reporting, dashboard push)
    are dispatched as async tasks to avoid blocking the control cycle.
    """

    def __init__(
        self,
        room_params: RoomParams | None = None,
        mpc_config: MPCConfig | None = None,
        weights: CostWeights | None = None,
        mqtt_client=None,
        redis_client=None,
        ws_server=None,
        location: str = "room1",
        device_id: str = "esp32-1",
        phase: str = "mid_flower",
        light_on_hour: float = 6.0,
        light_off_hour: float = 22.0,
    ):
        # Core MPC components
        self.model = GrowRoomModel(room_params)
        self.mpc = MPCSolver(
            model=self.model,
            config=mpc_config,
            weights=weights,
            room_params=room_params,
        )
        self.ekf = StateEstimator(model=self.model)
        self.reference = ReferenceGenerator(
            phase=phase,
            light_on_hour=light_on_hour,
            light_off_hour=light_off_hour,
            weights=weights,
        )
        self.compliance = ComplianceTracker()
        self.failsafe = FailsafeManager()

        # I/O interfaces
        self.sensors = SensorFusion(
            redis_client=redis_client,
            location=location,
            device_id=device_id,
        )
        self.actuators = ActuatorInterface(
            mqtt_client=mqtt_client,
            location=location,
            device_id=device_id,
        )

        # WebSocket for real-time dashboard updates
        self.ws_server = ws_server

        # State
        self._running = False
        self._last_u = self.model.default_input()
        self._current_disturbance = self.model.default_disturbance()

        # Performance monitoring
        self.solve_times: collections.deque = collections.deque(maxlen=1000)

        # Adaptive solve frequency
        self._solve_interval = 1.0  # seconds (default 1 Hz)
        self._last_solve_time = 0.0

        # Compliance reporting interval
        self._compliance_report_interval = 60.0  # every minute
        self._last_compliance_report = 0.0

    @property
    def is_running(self) -> bool:
        return self._running

    async def run(self) -> None:
        """Main control loop — runs at 1 Hz."""
        self._running = True
        logger.info("Control loop starting (1 Hz)")

        while self._running:
            t_cycle_start = time.perf_counter_ns()

            try:
                await self._control_cycle()
            except Exception as e:
                logger.error(f"Control cycle error: {e}", exc_info=True)
                self.failsafe.on_solve_failure(str(e))

            # Sleep remainder of cycle
            elapsed_s = (time.perf_counter_ns() - t_cycle_start) / 1e9
            sleep_time = max(0, self._solve_interval - elapsed_s)
            await asyncio.sleep(sleep_time)

        logger.info("Control loop stopped")

    async def _control_cycle(self) -> None:
        """Execute a single control cycle."""
        from datetime import datetime
        now = datetime.now()
        current_hour = now.hour + now.minute / 60.0

        # 1. Read sensors
        z_meas, available, is_stale = await self.sensors.read_sensors()

        # Adjust EKF noise if sensors are stale
        if is_stale:
            self.ekf.increase_process_noise()
        else:
            self.ekf.restore_process_noise()

        # 2. State estimation (EKF predict + update)
        x_hat, P = self.ekf.predict_and_update(
            z_meas, self._last_u, self._current_disturbance,
            dt=self._solve_interval, available=available,
        )

        # 3. Check failsafe timeout
        self.failsafe.check_timeout()

        # 4. Generate reference trajectory
        ref = self.reference.generate_reference(current_hour, self.mpc.Np, self.mpc.dt)
        d_forecast = self.reference.generate_disturbance_forecast(
            current_hour, self.mpc.Np, self.mpc.dt,
        )

        # 5. MPC solve
        result = self.mpc.solve(x_hat, d_forecast, ref, self._last_u)

        # 6. Apply through failsafe
        if result.feasible:
            u_apply = self.failsafe.on_solve_success(result.u_optimal)
        else:
            u_apply = self.failsafe.on_solve_failure(result.status)

        # 7. Apply control action
        await self.actuators.apply(u_apply)
        self._last_u = u_apply

        # 8. Track performance
        self.solve_times.append(result.total_ms)

        # 9. Update compliance (non-blocking)
        derived = self.model.derived_outputs(x_hat)
        self.compliance.update(x_hat, derived)

        # 10. Periodic compliance report
        t_now = time.time()
        if t_now - self._last_compliance_report >= self._compliance_report_interval:
            self._last_compliance_report = t_now
            asyncio.create_task(self._report_compliance())

        # 11. Broadcast state to dashboard (non-blocking)
        if self.ws_server:
            asyncio.create_task(self._broadcast_state(x_hat, derived, result))

    async def _report_compliance(self) -> None:
        """Generate and log compliance report."""
        report = self.compliance.report()
        if report:
            summary = {k: f"{v['compliance_pct']:.1f}%" for k, v in report.items()}
            logger.info(f"Compliance: {summary}")

    async def _broadcast_state(
        self,
        x_hat: np.ndarray,
        derived: dict[str, float],
        result: MPCSolveResult,
    ) -> None:
        """Broadcast current state to WebSocket clients."""
        if self.ws_server is None:
            return
        try:
            state = {
                "type": "mpc_state",
                "state": {k: round(v, 3) for k, v in derived.items()},
                "control": {
                    f"u_{i}": round(float(result.u_optimal[i]), 3)
                    for i in range(len(result.u_optimal))
                },
                "solve_ms": round(result.total_ms, 2),
                "status": result.status,
                "failsafe": self.failsafe.get_status(),
            }
            await self.ws_server.broadcast(state)
        except Exception as e:
            logger.debug(f"WebSocket broadcast failed: {e}")

    def on_telemetry(
        self,
        readings: dict[str, float],
        current_hour: float,
    ) -> MPCSolveResult | None:
        """Synchronous telemetry handler for MQTT callback integration.

        Can be called from the existing Orchestrator._handle_telemetry() path
        without requiring the async control loop to be running.

        Args:
            readings: Sensor readings dict {sensor_id: value}
            current_hour: Fractional hour of day

        Returns:
            MPCSolveResult or None on error
        """
        # Build measurement from readings
        z_meas, available, is_stale = self.sensors.read_sensors_sync(readings)

        # EKF predict + update
        x_hat, P = self.ekf.predict_and_update(
            z_meas, self._last_u, self._current_disturbance,
            dt=1.0, available=available,
        )

        # Generate reference
        ref = self.reference.generate_reference(current_hour, self.mpc.Np, self.mpc.dt)
        d_forecast = self.reference.generate_disturbance_forecast(
            current_hour, self.mpc.Np, self.mpc.dt,
        )

        # MPC solve
        result = self.mpc.solve(x_hat, d_forecast, ref, self._last_u)

        # Failsafe
        if result.feasible:
            u_apply = self.failsafe.on_solve_success(result.u_optimal)
        else:
            u_apply = self.failsafe.on_solve_failure(result.status)

        self._last_u = u_apply

        # Update compliance
        derived = self.model.derived_outputs(x_hat)
        self.compliance.update(x_hat, derived)

        # Track performance
        self.solve_times.append(result.total_ms)

        return result

    def stop(self) -> None:
        """Stop the control loop."""
        self._running = False

    def get_status(self) -> dict:
        """Return control loop status for monitoring."""
        solve_arr = list(self.solve_times)
        return {
            "running": self._running,
            "failsafe": self.failsafe.get_status(),
            "compliance": self.compliance.summary(),
            "solve_stats": {
                "count": len(solve_arr),
                "mean_ms": round(np.mean(solve_arr), 2) if solve_arr else 0,
                "p95_ms": round(np.percentile(solve_arr, 95), 2) if solve_arr else 0,
                "max_ms": round(max(solve_arr), 2) if solve_arr else 0,
            } if solve_arr else {},
        }
