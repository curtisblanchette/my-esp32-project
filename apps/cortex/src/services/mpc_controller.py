"""Production MPC controller — bridges live telemetry with the simulation MPC planner.

Calibrates the PhysicsEngine from real sensor readings, solves MPC, and
publishes optimal actuator commands via MQTT.
"""

import logging
import time

from simulations.physics import PhysicsEngine
from simulations.state_planner import MPCPlanner, MPCConfig, goals_from_dicts
from simulations.room_config import RoomConfig

from ..models.command import Command

logger = logging.getLogger(__name__)


class MPCController:
    """Production MPC controller that wraps PhysicsEngine + MPCPlanner."""

    def __init__(
        self,
        room_config: RoomConfig,
        goals: list[dict],
        mqtt_client,
        location: str,
        device_id: str,
    ) -> None:
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
            config=MPCConfig(),
            goals=goal_specs,
            actuator_specs=room_config.actuator_specs,
        )
        self.mqtt = mqtt_client
        self.location = location
        self.device_id = device_id
        self._last_solve_time = 0.0
        self._solve_interval = 30.0  # seconds

    def on_telemetry(
        self,
        device_id: str,
        readings: dict[str, float],
        current_hour: float,
    ) -> list[Command]:
        """Calibrate model, solve MPC, return commands."""
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

    def reload_goals(self, goals: list[dict]) -> None:
        """Hot-reload goals from DB (e.g. after API update)."""
        self.planner._goals = goals_from_dicts(goals)
        self.planner.reset_warm_start()
