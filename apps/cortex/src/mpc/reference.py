"""Phase-dependent reference trajectory generation and weight matrices.

Generates target setpoints for each prediction step based on growth phase,
light state, cultivar, and time of day. Also provides Q, R, S weight matrices
for the MPC cost function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from .model import NX, NU, StateIndex


# ---------------------------------------------------------------------------
# Phase targets
# ---------------------------------------------------------------------------

@dataclass
class PhaseTargets:
    """Target setpoints for a growth phase / light condition."""
    vpd: float = 1.2            # kPa
    t_air: float = 26.0         # °C
    rh: float = 55.0            # %
    co2: float = 1200.0         # ppm
    dryback_rate: float = -0.015  # m³/m³/hr
    theta: float = 0.35         # target VWC


# Default phase target presets
PHASE_DEFAULTS: dict[str, dict[str, PhaseTargets]] = {
    "clone": {
        "day":   PhaseTargets(vpd=0.6, t_air=24.0, rh=75.0, co2=800.0, dryback_rate=-0.005, theta=0.40),
        "night": PhaseTargets(vpd=0.4, t_air=22.0, rh=80.0, co2=420.0, dryback_rate=-0.003, theta=0.40),
    },
    "veg": {
        "day":   PhaseTargets(vpd=0.9, t_air=26.0, rh=60.0, co2=1000.0, dryback_rate=-0.010, theta=0.38),
        "night": PhaseTargets(vpd=0.6, t_air=22.0, rh=65.0, co2=420.0, dryback_rate=-0.005, theta=0.38),
    },
    "early_flower": {
        "day":   PhaseTargets(vpd=1.0, t_air=26.0, rh=55.0, co2=1200.0, dryback_rate=-0.012, theta=0.36),
        "night": PhaseTargets(vpd=0.7, t_air=22.0, rh=60.0, co2=420.0, dryback_rate=-0.005, theta=0.36),
    },
    "mid_flower": {
        "day":   PhaseTargets(vpd=1.2, t_air=26.0, rh=50.0, co2=1200.0, dryback_rate=-0.015, theta=0.35),
        "night": PhaseTargets(vpd=0.8, t_air=21.0, rh=55.0, co2=420.0, dryback_rate=-0.008, theta=0.35),
    },
    "late_flower": {
        "day":   PhaseTargets(vpd=1.3, t_air=24.0, rh=45.0, co2=1000.0, dryback_rate=-0.018, theta=0.30),
        "night": PhaseTargets(vpd=0.9, t_air=20.0, rh=50.0, co2=420.0, dryback_rate=-0.010, theta=0.30),
    },
    "dry": {
        "day":   PhaseTargets(vpd=1.0, t_air=20.0, rh=55.0, co2=420.0, dryback_rate=-0.005, theta=0.25),
        "night": PhaseTargets(vpd=1.0, t_air=18.0, rh=55.0, co2=420.0, dryback_rate=-0.005, theta=0.25),
    },
}

# Output tracking weight names matching PhaseTargets fields
OUTPUT_NAMES = ["VPD", "T_air", "CO2", "dryback_rate", "theta", "T_leaf", "T_supply"]


# ---------------------------------------------------------------------------
# Weight matrices
# ---------------------------------------------------------------------------

@dataclass
class CostWeights:
    """Diagonal weight values for Q, R, S matrices."""
    # Output tracking (Q diagonal)
    q_vpd: float = 100.0
    q_t_air: float = 10.0
    q_co2: float = 5.0
    q_dryback: float = 50.0
    q_theta: float = 20.0
    q_t_leaf: float = 1.0
    q_t_supply: float = 0.1

    # Input cost (R diagonal) — energy cost
    r_cool: float = 1.0
    r_heat: float = 0.8
    r_dehum: float = 0.6
    r_hum: float = 0.1
    r_co2: float = 0.3
    r_fan: float = 0.2
    r_irr: float = 0.0

    # Input rate-of-change (S diagonal) — smoothness
    s_cool: float = 50.0
    s_heat: float = 20.0
    s_dehum: float = 30.0
    s_hum: float = 5.0
    s_co2: float = 10.0
    s_fan: float = 40.0
    s_irr: float = 1.0

    # Soft constraint penalty
    soft_penalty: float = 1000.0

    def Q_diag(self) -> np.ndarray:
        """State tracking weight diagonal (7 elements, maps to state indices)."""
        return np.array([
            self.q_t_air,       # T_air
            0.0,                # w_air (tracked via VPD, not directly)
            self.q_co2,         # CO2
            self.q_t_leaf,      # T_leaf
            self.q_theta,       # theta
            self.q_t_supply,    # T_supply
            0.0,                # T_wall (not tracked)
        ])

    def R_diag(self) -> np.ndarray:
        """Input cost weight diagonal (7 elements)."""
        return np.array([
            self.r_cool, self.r_heat, self.r_dehum, self.r_hum,
            self.r_co2, self.r_fan, self.r_irr,
        ])

    def S_diag(self) -> np.ndarray:
        """Input rate-of-change weight diagonal (7 elements)."""
        return np.array([
            self.s_cool, self.s_heat, self.s_dehum, self.s_hum,
            self.s_co2, self.s_fan, self.s_irr,
        ])


# ---------------------------------------------------------------------------
# Reference generator
# ---------------------------------------------------------------------------

class ReferenceGenerator:
    """Generates target reference trajectories for the MPC solver.

    Produces a reference vector for each prediction step based on the
    current growth phase, light schedule, and cultivar profile.
    """

    def __init__(
        self,
        phase: str = "mid_flower",
        light_on_hour: float = 6.0,
        light_off_hour: float = 22.0,
        cultivar_name: str = "default",
        weights: CostWeights | None = None,
        phase_targets: dict[str, dict[str, PhaseTargets]] | None = None,
    ):
        self.phase = phase
        self.light_on = light_on_hour
        self.light_off = light_off_hour
        self.cultivar = cultivar_name
        self.weights = weights or CostWeights()
        self._targets = phase_targets or PHASE_DEFAULTS

    def is_lights_on(self, hour: float) -> bool:
        """Check if lights are on at the given hour."""
        if self.light_on < self.light_off:
            return self.light_on <= hour < self.light_off
        else:
            return hour >= self.light_on or hour < self.light_off

    def get_targets(self, hour: float) -> PhaseTargets:
        """Get target setpoints for a given time of day."""
        light_state = "day" if self.is_lights_on(hour) else "night"
        phase_dict = self._targets.get(self.phase, PHASE_DEFAULTS.get("mid_flower", {}))
        return phase_dict.get(light_state, PhaseTargets())

    def generate_reference(
        self,
        current_hour: float,
        Np: int = 60,
        dt: float = 60.0,
    ) -> np.ndarray:
        """Generate state reference trajectory over prediction horizon.

        Args:
            current_hour: Current time of day (0-24)
            Np: Number of prediction steps
            dt: Time step in seconds

        Returns:
            reference: State reference trajectory (Np × nx)
        """
        ref = np.zeros((Np, NX))
        step_hours = dt / 3600.0

        for k in range(Np):
            hour = (current_hour + k * step_hours) % 24.0
            targets = self.get_targets(hour)

            # Map targets to state vector indices
            ref[k, StateIndex.T_AIR] = targets.t_air
            # w_air: derive from target RH and target T
            # w_sat(T) = 622 × SVP(T) / (P_ATM - SVP(T))
            from .model import svp, P_ATM
            es = svp(targets.t_air)
            w_sat_val = 622.0 * es / (P_ATM - es)
            ref[k, StateIndex.W_AIR] = w_sat_val * targets.rh / 100.0
            ref[k, StateIndex.CO2] = targets.co2
            ref[k, StateIndex.T_LEAF] = targets.t_air - 1.0  # leaf slightly cooler
            ref[k, StateIndex.THETA] = targets.theta
            ref[k, StateIndex.T_SUPPLY] = targets.t_air - 5.0  # supply cooler than room
            ref[k, StateIndex.T_WALL] = targets.t_air - 1.0

        return ref

    def generate_disturbance_forecast(
        self,
        current_hour: float,
        Np: int = 60,
        dt: float = 60.0,
        t_outdoor: float = 25.0,
        w_outdoor: float = 8.0,
        light_watts: float = 300.0,
    ) -> np.ndarray:
        """Generate disturbance forecast over prediction horizon.

        Args:
            current_hour: Current time of day (0-24)
            Np: Number of prediction steps
            dt: Time step in seconds
            t_outdoor: Outdoor temperature (°C)
            w_outdoor: Outdoor absolute humidity (g/kg)
            light_watts: Light power when on (W)

        Returns:
            d_forecast: Disturbance forecast (Np × nd)
        """
        from .model import ND
        d = np.zeros((Np, ND))
        step_hours = dt / 3600.0

        for k in range(Np):
            hour = (current_hour + k * step_hours) % 24.0
            d[k, 0] = light_watts if self.is_lights_on(hour) else 0.0
            d[k, 1] = t_outdoor
            d[k, 2] = w_outdoor
            d[k, 3] = 4.0   # N_plants (constant)
            d[k, 4] = 2.0   # phase index (constant)

        return d

    def set_phase(self, phase: str) -> None:
        """Update growth phase (triggers reference recalculation)."""
        self.phase = phase

    def set_light_schedule(self, on_hour: float, off_hour: float) -> None:
        """Update light schedule."""
        self.light_on = on_hour
        self.light_off = off_hour

    @classmethod
    def from_yaml(cls, path: str | Path, phase: str = "mid_flower") -> ReferenceGenerator:
        """Load phase targets from a YAML config directory."""
        config_dir = Path(path)
        phase_targets: dict[str, dict[str, PhaseTargets]] = {}

        phases_dir = config_dir / "phases"
        if phases_dir.exists():
            for yaml_file in phases_dir.glob("*.yaml"):
                phase_name = yaml_file.stem
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                if data:
                    phase_targets[phase_name] = {}
                    for light_state in ("day", "night"):
                        if light_state in data:
                            d = data[light_state]
                            phase_targets[phase_name][light_state] = PhaseTargets(
                                vpd=d.get("vpd", 1.2),
                                t_air=d.get("t_air", 26.0),
                                rh=d.get("rh", 55.0),
                                co2=d.get("co2", 1200.0),
                                dryback_rate=d.get("dryback_rate", -0.015),
                                theta=d.get("theta", 0.35),
                            )

        # Load light schedule from cultivar or default
        light_on = 6.0
        light_off = 22.0
        cultivar_dir = config_dir / "cultivar"
        if cultivar_dir.exists():
            default_cultivar = cultivar_dir / "default.yaml"
            if default_cultivar.exists():
                with open(default_cultivar) as f:
                    cdata = yaml.safe_load(f)
                if cdata:
                    light_on = cdata.get("light_on_hour", 6.0)
                    light_off = cdata.get("light_off_hour", 22.0)

        return cls(
            phase=phase,
            light_on_hour=light_on,
            light_off_hour=light_off,
            phase_targets=phase_targets or None,
        )
