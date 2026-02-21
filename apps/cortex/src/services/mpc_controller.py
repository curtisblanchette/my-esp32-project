"""Production MPC controller — bridges live telemetry with MPC solvers.

Supports two modes:
- "slsqp" (legacy): PhysicsEngine + SLSQP nonlinear shooting via scipy
- "osqp" (new): 7-state linear model + OSQP convex QP solver with EKF

Set MPC_MODE=osqp to enable the new sub-100ms predictive controller.
"""

import logging
import time

from ..models.command import Command

logger = logging.getLogger(__name__)


class MPCController:
    """Production MPC controller that wraps either legacy SLSQP or new OSQP solver."""

    def __init__(
        self,
        room_config=None,
        goals: list[dict] | None = None,
        mqtt_client=None,
        location: str = "room1",
        device_id: str = "esp32-1",
        mode: str = "slsqp",
        phase: str = "mid_flower",
        config_path: str = "config",
    ) -> None:
        self.mqtt = mqtt_client
        self.location = location
        self.device_id = device_id
        self.mode = mode
        self._last_solve_time = 0.0

        if mode == "osqp":
            self._init_osqp(phase, config_path)
        else:
            self._init_slsqp(room_config, goals or [])

    def _init_slsqp(self, room_config, goals: list[dict]) -> None:
        """Initialize legacy SLSQP-based MPC."""
        from simulations.physics import PhysicsEngine
        from simulations.state_planner import MPCPlanner, MPCConfig as LegacyMPCConfig, goals_from_dicts
        from simulations.room_config import RoomConfig

        self.physics = PhysicsEngine(
            tent=room_config.space,
            actuator_specs=room_config.actuator_specs,
            ambient_temp=room_config.ambient_temp,
            ambient_schedule=room_config.ambient_schedule_fn,
            plant=room_config.plant,
            ventilation_fn=room_config.ventilation_fn,
            substrate_fn=room_config.substrate_fn,
            substrate_config=room_config.substrate_config,
            substrate_container=room_config.substrate_container,
            noise=False,
        )
        goal_specs = goals_from_dicts(goals)
        self.planner = MPCPlanner(
            config=LegacyMPCConfig(),
            goals=goal_specs,
            actuator_specs=room_config.actuator_specs,
        )
        self._solve_interval = 30.0

    def _init_osqp(self, phase: str, config_path: str) -> None:
        """Initialize OSQP-based MPC with EKF and control loop."""
        from ..control.loop import ControlLoop

        self._control_loop = ControlLoop(
            phase=phase,
            location=self.location,
            device_id=self.device_id,
        )
        self._solve_interval = 1.0  # 1 Hz
        logger.info(f"OSQP MPC initialized (phase={phase})")

    def on_telemetry(
        self,
        device_id: str,
        readings: dict[str, float],
        current_hour: float,
    ) -> list[Command]:
        """Process telemetry and generate actuator commands."""
        now = time.time()

        # Rate limit based on mode
        if now - self._last_solve_time < self._solve_interval:
            return []

        if self.mode == "osqp":
            return self._on_telemetry_osqp(device_id, readings, current_hour)
        else:
            return self._on_telemetry_slsqp(device_id, readings, current_hour)

    def _on_telemetry_slsqp(
        self,
        device_id: str,
        readings: dict[str, float],
        current_hour: float,
    ) -> list[Command]:
        """Legacy SLSQP path."""
        self.physics.calibrate(readings)
        result = self.planner.solve(self.physics, current_hour)
        commands: list[Command] = []
        for actuator, intensity in result.optimal_intensities.items():
            cmd = Command(
                device_id=device_id,
                location=self.location,
                target=actuator,
                action="set",
                value=intensity,
                reason=f"MPC optimal (cost={result.cost:.3f})",
            )
            commands.append(cmd)
            if self.mqtt:
                self.mqtt.publish_command(cmd)
        self._last_solve_time = time.time()
        return commands

    def _on_telemetry_osqp(
        self,
        device_id: str,
        readings: dict[str, float],
        current_hour: float,
    ) -> list[Command]:
        """OSQP path — uses EKF + QP solver."""
        from ..mpc.model import InputIndex

        result = self._control_loop.on_telemetry(readings, current_hour)
        if result is None:
            return []

        # Map control vector to commands
        actuator_map = {
            InputIndex.COOL: "hvac_cool",
            InputIndex.HEAT: "hvac_heat",
            InputIndex.DEHUM: "dehumidifier",
            InputIndex.HUM: "humidifier",
            InputIndex.CO2_INJ: "co2_injector",
            InputIndex.FAN: "fan",
            InputIndex.IRR: "irrigation",
        }

        commands: list[Command] = []
        for idx, target in actuator_map.items():
            value = float(result.u_optimal[idx])
            cmd = Command(
                device_id=device_id,
                location=self.location,
                target=target,
                action="set",
                value=value,
                reason=f"MPC-OSQP ({result.status}, {result.total_ms:.1f}ms)",
            )
            commands.append(cmd)
            if self.mqtt:
                self.mqtt.publish_command(cmd)

        self._last_solve_time = time.time()
        return commands

    def reload_goals(self, goals: list[dict]) -> None:
        """Hot-reload goals from DB."""
        if self.mode == "slsqp":
            from simulations.state_planner import goals_from_dicts
            self.planner._goals = goals_from_dicts(goals)
            self.planner.reset_warm_start()
        else:
            # OSQP mode: update reference generator phase/targets
            logger.info("OSQP goal reload — reference generator update not yet implemented")

    def get_status(self) -> dict:
        """Return controller status for monitoring API."""
        if self.mode == "osqp":
            return {
                "mode": "osqp",
                **self._control_loop.get_status(),
            }
        return {
            "mode": "slsqp",
            "last_solve_age_s": round(time.time() - self._last_solve_time, 1),
        }
