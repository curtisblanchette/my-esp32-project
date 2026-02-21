"""Control loop infrastructure for MPC-driven environment management.

Provides:
- 1 Hz asyncio control loop
- Sensor fusion with staleness detection
- Actuator interface (MQTT + future Modbus/0-10V)
- Fail-safe graceful degradation
"""

from .loop import ControlLoop
from .sensors import SensorFusion
from .actuators import ActuatorInterface
from .failsafe import FailsafeManager, ControlPriority

__all__ = [
    "ControlLoop",
    "SensorFusion",
    "ActuatorInterface",
    "FailsafeManager",
    "ControlPriority",
]
