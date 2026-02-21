"""Actuator interface — translates MPC control vector to MQTT commands.

Maps the 7-element control vector [u_cool, u_heat, u_dehum, u_hum, u_co2,
u_fan, u_irr] to actuator commands published via MQTT. Supports quantization
for binary actuators (relays) and continuous actuators (0-10V/Modbus).
"""

from __future__ import annotations

import logging

import numpy as np

from ..mpc.model import InputIndex
from ..models.command import Command

logger = logging.getLogger(__name__)

# Mapping from MPC control index to actuator name
INPUT_TO_ACTUATOR: dict[int, str] = {
    InputIndex.COOL: "hvac",            # or cooling-specific relay
    InputIndex.HEAT: "hvac",            # heating mode
    InputIndex.DEHUM: "dehumidifier",
    InputIndex.HUM: "humidifier",
    InputIndex.CO2_INJ: "co2_injector",
    InputIndex.FAN: "fan",
    InputIndex.IRR: "irrigation",
}

# Actuators that are binary (relay) — snap to 0 or 1
BINARY_ACTUATORS = {"co2_injector", "irrigation"}

# Minimum change threshold to avoid MQTT spam
MIN_CHANGE_THRESHOLD = 0.02


class ActuatorInterface:
    """Translates MPC control output to actuator commands.

    Handles:
    - Binary quantization for relay actuators
    - Deadband filtering to avoid command spam
    - MQTT command publishing
    - Command tracking
    """

    def __init__(
        self,
        mqtt_client=None,
        location: str = "room1",
        device_id: str = "esp32-1",
    ):
        self.mqtt = mqtt_client
        self.location = location
        self.device_id = device_id

        # Track current actuator states for deadband filtering
        self._current: dict[str, float] = {}

    async def apply(self, u: np.ndarray) -> list[Command]:
        """Apply control vector to actuators.

        Args:
            u: Control vector [u_cool, u_heat, u_dehum, u_hum, u_co2, u_fan, u_irr]

        Returns:
            List of Command objects that were published
        """
        commands = []

        for idx, actuator_name in INPUT_TO_ACTUATOR.items():
            raw_value = float(u[idx])

            # Quantize binary actuators
            if actuator_name in BINARY_ACTUATORS:
                value = 1.0 if raw_value >= 0.5 else 0.0
            else:
                value = max(0.0, min(1.0, raw_value))

            # Deadband filtering: skip if change is negligible
            prev = self._current.get(actuator_name, -1.0)
            if abs(value - prev) < MIN_CHANGE_THRESHOLD:
                continue

            self._current[actuator_name] = value

            # HVAC mode handling: cool vs heat
            if idx == InputIndex.COOL:
                target = "hvac_cool"
                reason = f"MPC cooling ({value:.2f})"
            elif idx == InputIndex.HEAT:
                target = "hvac_heat"
                reason = f"MPC heating ({value:.2f})"
            else:
                target = actuator_name
                reason = f"MPC {actuator_name} ({value:.2f})"

            cmd = Command(
                device_id=self.device_id,
                location=self.location,
                target=target,
                action="set",
                value=value,
                reason=reason,
            )
            commands.append(cmd)

            if self.mqtt:
                try:
                    self.mqtt.publish_command(cmd)
                except Exception as e:
                    logger.warning(f"Failed to publish command for {target}: {e}")

        return commands

    def apply_sync(self, u: np.ndarray) -> list[Command]:
        """Synchronous version of apply() for non-async contexts."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't await in running loop — do sync
                return self._apply_sync_inner(u)
            return loop.run_until_complete(self.apply(u))
        except RuntimeError:
            return self._apply_sync_inner(u)

    def _apply_sync_inner(self, u: np.ndarray) -> list[Command]:
        """Inner sync apply without async."""
        commands = []

        for idx, actuator_name in INPUT_TO_ACTUATOR.items():
            raw_value = float(u[idx])

            if actuator_name in BINARY_ACTUATORS:
                value = 1.0 if raw_value >= 0.5 else 0.0
            else:
                value = max(0.0, min(1.0, raw_value))

            prev = self._current.get(actuator_name, -1.0)
            if abs(value - prev) < MIN_CHANGE_THRESHOLD:
                continue

            self._current[actuator_name] = value

            if idx == InputIndex.COOL:
                target = "hvac_cool"
                reason = f"MPC cooling ({value:.2f})"
            elif idx == InputIndex.HEAT:
                target = "hvac_heat"
                reason = f"MPC heating ({value:.2f})"
            else:
                target = actuator_name
                reason = f"MPC {actuator_name} ({value:.2f})"

            cmd = Command(
                device_id=self.device_id,
                location=self.location,
                target=target,
                action="set",
                value=value,
                reason=reason,
            )
            commands.append(cmd)

            if self.mqtt:
                try:
                    self.mqtt.publish_command(cmd)
                except Exception as e:
                    logger.warning(f"Failed to publish command for {target}: {e}")

        return commands

    def get_current_states(self) -> dict[str, float]:
        """Return current actuator states."""
        return dict(self._current)

    def reset(self) -> None:
        """Reset actuator tracking (forces all commands to be re-sent)."""
        self._current.clear()
