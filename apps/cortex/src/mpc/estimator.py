"""Extended Kalman Filter for state estimation.

Fuses noisy sensor readings into clean state estimates for the MPC solver.
Handles missing sensors gracefully by removing corresponding rows from the
observation matrix.

Measurements (up to 6):
    z = [T_air, w_air_derived, CO2, T_leaf, theta, T_supply]

The w_air measurement is derived from RH sensor + T_air: w = w_sat(T) × RH/100.
T_leaf may be unavailable (no IR sensor) — handled by dynamic observation matrix.
"""

from __future__ import annotations

import logging

import numpy as np

from .model import GrowRoomModel, NX, NU, RoomParams, w_sat

logger = logging.getLogger(__name__)

# Maximum number of measurements
NZ_MAX = 6

# Measurement indices (when all sensors available)
Z_T_AIR = 0
Z_W_AIR = 1
Z_CO2 = 2
Z_T_LEAF = 3
Z_THETA = 4
Z_T_SUPPLY = 5


class StateEstimator:
    """Extended Kalman Filter for 7-state grow room model.

    Runs at sensor rate (1 Hz). Predict step propagates state through
    nonlinear dynamics; update step fuses sensor measurements via
    linearized observation model.
    """

    def __init__(
        self,
        model: GrowRoomModel | None = None,
        room_params: RoomParams | None = None,
    ):
        self.model = model or GrowRoomModel(room_params)
        self.nx = NX

        # State estimate and covariance
        self.x_hat = self.model.default_state()
        self.P = np.eye(NX) * 10.0

        # Process noise covariance (trust in model)
        self.Q = np.diag([
            0.01,       # T_air: model is good
            0.005,      # w_air: model is decent
            5.0,        # CO2: injection is discrete, higher noise
            0.02,       # T_leaf: simplified model, moderate noise
            0.001,      # theta: model is good between irrigation events
            0.05,       # T_supply: HVAC model is approximate
            0.001,      # T_wall: very slow state, low noise
        ])

        # Measurement noise covariance (trust in sensors)
        # Diagonal entries are σ² for each sensor
        self.R = np.diag([
            0.09,       # T_air: SHT31 ±0.3°C → σ² = 0.09
            0.04,       # w_air: derived from RH, ±0.2 g/kg
            900.0,      # CO2: SCD30 ±30 ppm → σ² = 900
            0.25,       # T_leaf: MLX90614 ±0.5°C → σ² = 0.25
            0.0001,     # theta: TEROS ±0.01 m³/m³ → σ² = 0.0001
            0.09,       # T_supply: thermocouple ±0.3°C → σ² = 0.09
        ])

        # Staleness tracking
        self._last_update_time: dict[str, float] = {}

    def predict(
        self,
        u_applied: np.ndarray,
        d_current: np.ndarray,
        dt: float = 1.0,
    ) -> None:
        """EKF predict step: propagate state and covariance forward.

        Args:
            u_applied: Control input currently being applied (7,)
            d_current: Current disturbance vector (5,)
            dt: Time step in seconds
        """
        # Propagate state through nonlinear dynamics
        dxdt = self.model.dynamics(self.x_hat, u_applied, d_current)
        x_pred = self.x_hat + dxdt * dt

        # Linearize for covariance propagation
        eps_x = np.array([0.01, 0.01, 1.0, 0.01, 0.001, 0.01, 0.01])
        F = np.zeros((NX, NX))
        for j in range(NX):
            x_plus = self.x_hat.copy()
            x_minus = self.x_hat.copy()
            x_plus[j] += eps_x[j]
            x_minus[j] -= eps_x[j]
            f_plus = self.model.dynamics(x_plus, u_applied, d_current)
            f_minus = self.model.dynamics(x_minus, u_applied, d_current)
            F[:, j] = (f_plus - f_minus) / (2.0 * eps_x[j])

        # Discrete Jacobian
        F_d = np.eye(NX) + F * dt

        # Propagate covariance
        self.P = F_d @ self.P @ F_d.T + self.Q * dt
        self.x_hat = x_pred

    def update(
        self,
        z_meas: np.ndarray,
        available: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """EKF update step: fuse sensor measurements.

        Args:
            z_meas: Measurement vector (up to 6 elements).
                    Unavailable sensors should have NaN.
            available: Boolean mask of available sensors (6,).
                      If None, inferred from non-NaN values in z_meas.

        Returns:
            x_hat: Updated state estimate (7,)
            P: Updated covariance (7×7)
        """
        if available is None:
            available = ~np.isnan(z_meas)

        n_avail = int(np.sum(available))
        if n_avail == 0:
            return self.x_hat.copy(), self.P.copy()

        # Build observation matrix H for available sensors
        # Full H maps state → measurement: z = H·x (linear for most states)
        H_full = np.zeros((NZ_MAX, NX))
        H_full[Z_T_AIR, 0] = 1.0       # T_air → T_air
        H_full[Z_W_AIR, 1] = 1.0       # w_air → w_air
        H_full[Z_CO2, 2] = 1.0         # CO2 → CO2
        H_full[Z_T_LEAF, 3] = 1.0      # T_leaf → T_leaf
        H_full[Z_THETA, 4] = 1.0       # theta → theta
        H_full[Z_T_SUPPLY, 5] = 1.0    # T_supply → T_supply

        # Select only available rows
        H = H_full[available]
        R = self.R[np.ix_(available, available)]
        z = z_meas[available]

        # Predicted measurement
        z_pred = H @ self.x_hat

        # Innovation
        innovation = z - z_pred

        # Innovation covariance
        S = H @ self.P @ H.T + R

        # Kalman gain
        try:
            K = self.P @ H.T @ np.linalg.inv(S)
        except np.linalg.LinAlgError:
            logger.warning("Kalman gain computation failed (singular S)")
            return self.x_hat.copy(), self.P.copy()

        # State update
        self.x_hat = self.x_hat + K @ innovation

        # Covariance update (Joseph form for numerical stability)
        I_KH = np.eye(NX) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ R @ K.T

        # Ensure symmetry
        self.P = (self.P + self.P.T) / 2.0

        return self.x_hat.copy(), self.P.copy()

    def predict_and_update(
        self,
        z_meas: np.ndarray,
        u_applied: np.ndarray,
        d_current: np.ndarray,
        dt: float = 1.0,
        available: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Combined predict + update cycle (convenience method).

        Args:
            z_meas: Measurement vector (6,)
            u_applied: Applied control input (7,)
            d_current: Current disturbances (5,)
            dt: Time step (seconds)
            available: Boolean mask of available sensors

        Returns:
            x_hat: Updated state estimate (7,)
            P: Updated covariance (7×7)
        """
        self.predict(u_applied, d_current, dt)
        return self.update(z_meas, available)

    def build_measurement(
        self,
        readings: dict[str, float],
        sensor_map: dict[str, str] | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build measurement vector from sensor readings dict.

        Args:
            readings: Sensor readings {sensor_id: value}
            sensor_map: Mapping from sensor_id to measurement name.
                       Defaults to ESP32 naming convention.

        Returns:
            z_meas: Measurement vector (6,) with NaN for missing
            available: Boolean mask (6,)
        """
        if sensor_map is None:
            sensor_map = {
                "temp1": "T_air",
                "hum1": "RH",  # will be converted to w_air
                "co2_1": "CO2",
                "leaf_temp1": "T_leaf",
                "soil1": "theta",
                "supply_temp1": "T_supply",
            }

        z_meas = np.full(NZ_MAX, np.nan)

        for sensor_id, value in readings.items():
            meas_name = sensor_map.get(sensor_id)
            if meas_name is None:
                continue

            if meas_name == "T_air":
                z_meas[Z_T_AIR] = value
            elif meas_name == "RH":
                # Convert RH% → absolute humidity (g/kg)
                t_air = readings.get("temp1", self.x_hat[0])
                ws = w_sat(t_air)
                z_meas[Z_W_AIR] = ws * value / 100.0
            elif meas_name == "CO2":
                z_meas[Z_CO2] = value
            elif meas_name == "T_leaf":
                z_meas[Z_T_LEAF] = value
            elif meas_name == "theta":
                z_meas[Z_THETA] = value
            elif meas_name == "T_supply":
                z_meas[Z_T_SUPPLY] = value

        available = ~np.isnan(z_meas)
        return z_meas, available

    def reset(self, x0: np.ndarray | None = None) -> None:
        """Reset estimator state."""
        self.x_hat = x0 if x0 is not None else self.model.default_state()
        self.P = np.eye(NX) * 10.0

    def increase_process_noise(self, factor: float = 5.0) -> None:
        """Temporarily increase process noise (e.g., stale sensors)."""
        self.Q *= factor

    def restore_process_noise(self) -> None:
        """Restore default process noise."""
        self.Q = np.diag([0.01, 0.005, 5.0, 0.02, 0.001, 0.05, 0.001])
