"""MPC (Model Predictive Control) subsystem — OSQP-based convex QP solver.

Provides sub-100ms predictive environment control with:
- 7-state thermodynamic model with analytical Jacobians
- OSQP convex QP solver with warm-starting
- Extended Kalman Filter for state estimation
- Phase-dependent reference trajectory generation
- Real-time compliance tracking
"""

from .model import GrowRoomModel, StateIndex, InputIndex
from .solver import MPCSolver, MPCSolveResult
from .estimator import StateEstimator
from .reference import ReferenceGenerator
from .constraints import ConstraintBuilder
from .compliance import ComplianceTracker

__all__ = [
    "GrowRoomModel",
    "StateIndex",
    "InputIndex",
    "MPCSolver",
    "MPCSolveResult",
    "StateEstimator",
    "ReferenceGenerator",
    "ConstraintBuilder",
    "ComplianceTracker",
]
