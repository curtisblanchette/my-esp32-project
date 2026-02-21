"""
Ecosystem health scoring engine.

Computes a weighted compliance score across all goals in a grow profile.
Supports raw sensor goals, derived metric goals (VPD), and relay schedule goals.
Strategy-adjusted tolerance widens or narrows acceptable ranges.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

from .derived_metrics import compute_derived

logger = logging.getLogger(__name__)

# Strategy tolerance multipliers — precision is strict, efficiency is lenient
STRATEGY_TOLERANCE_MULTIPLIER = {
    "precision": 0.0,
    "balanced": 1.0,
    "efficiency": 2.0,
}


@dataclass
class EnergySnapshot:
    """Energy consumption for a time window."""
    total_wh: float
    per_actuator_wh: dict[str, float]
    per_actuator_runtime_min: dict[str, float]


@dataclass
class EcosystemHealth:
    """Result of an ecosystem health assessment."""
    score: float                         # 0.0 to 1.0 — weighted compliance
    goal_scores: dict[str, float]        # per-metric compliance (0.0 to 1.0)
    out_of_range: list[str]              # metrics currently outside their goal
    energy: EnergySnapshot | None        # real Wh if actuator data provided
    timestamp: int


def effective_range(
    range_min: float | None,
    range_max: float | None,
    tolerance: float,
    strategy: str,
) -> tuple[float | None, float | None]:
    """
    Compute effective goal range adjusted by strategy tolerance.

    Precision: no tolerance — exact range.
    Balanced: use configured tolerance.
    Efficiency: double the tolerance — accept wider range.
    """
    mult = STRATEGY_TOLERANCE_MULTIPLIER.get(strategy, 1.0)
    margin = tolerance * mult
    eff_min = (range_min - margin) if range_min is not None else None
    eff_max = (range_max + margin) if range_max is not None else None
    return (eff_min, eff_max)


def score_goal(
    value: float,
    range_min: float | None,
    range_max: float | None,
    tolerance: float,
    strategy: str,
) -> float:
    """
    Score a single metric against its goal range.

    Returns 1.0 if in range, degrades linearly toward 0.0 as the value
    moves further from the effective range.
    """
    eff_min, eff_max = effective_range(range_min, range_max, tolerance, strategy)

    # Check if in range
    below = eff_min is not None and value < eff_min
    above = eff_max is not None and value > eff_max

    if not below and not above:
        return 1.0

    # Compute distance from range
    if below:
        distance = eff_min - value
    else:
        distance = value - eff_max

    # Scale: score degrades over the range width (or tolerance if open-ended)
    if eff_min is not None and eff_max is not None:
        scale = eff_max - eff_min
    elif tolerance > 0:
        scale = tolerance * 4  # reasonable degradation rate for open-ended
    else:
        scale = abs(value) * 0.2 if value != 0 else 1.0

    if scale <= 0:
        return 0.0

    return max(0.0, 1.0 - (distance / scale))


def score_relay_schedule(
    relay_state: bool,
    schedule: dict,
    now: datetime | None = None,
) -> float:
    """
    Score a relay against its expected schedule.

    Schedule format: {"06:00-22:00": {"expected": true}, "22:00-06:00": {"expected": false}}
    Returns 1.0 if relay matches expected state, 0.0 if not.
    """
    if now is None:
        now = datetime.now()

    current_time = now.strftime("%H:%M")

    for time_range, config in schedule.items():
        parts = time_range.split("-")
        if len(parts) != 2:
            continue
        start, end = parts[0].strip(), parts[1].strip()

        # Handle overnight ranges (e.g., "22:00-06:00")
        if start <= end:
            in_range = start <= current_time < end
        else:
            in_range = current_time >= start or current_time < end

        if in_range:
            expected = config.get("expected", True)
            return 1.0 if relay_state == expected else 0.0

    # No matching schedule window — assume compliant
    return 1.0


def compute_energy(
    actuator_runtime: dict[str, float],
    actuator_watts: dict[str, float],
) -> EnergySnapshot:
    """
    Compute energy consumption from actuator runtime and rated wattage.

    Args:
        actuator_runtime: relay_id → minutes ON in window.
        actuator_watts: relay_id → rated watts.
    """
    per_actuator_wh = {}
    for relay_id, minutes in actuator_runtime.items():
        watts = actuator_watts.get(relay_id, 25.0)  # fallback 25W
        per_actuator_wh[relay_id] = round(watts * (minutes / 60.0), 3)

    return EnergySnapshot(
        total_wh=round(sum(per_actuator_wh.values()), 3),
        per_actuator_wh=per_actuator_wh,
        per_actuator_runtime_min=dict(actuator_runtime),
    )


def compute_health(
    profile: dict,
    goals: list[dict],
    readings: dict[str, float],
    relay_states: dict[str, bool] | None = None,
    actuator_runtime: dict[str, float] | None = None,
    actuator_watts: dict[str, float] | None = None,
    history: dict[str, tuple[list[float], list[int]]] | None = None,
    now: datetime | None = None,
) -> EcosystemHealth:
    """
    Compute ecosystem health score across all goals.

    Args:
        profile: Grow profile dict (must have 'strategy', 'phase').
        goals: List of goal dicts for the profile.
        readings: Current sensor values {sensor_id: value}.
        relay_states: Current relay states {relay_id: bool}.
        actuator_runtime: Minutes ON per relay in current window.
        actuator_watts: Rated watts per relay.
        history: Per-sensor history for derived metrics.
        now: Current time (for relay schedule checks).
    """
    strategy = profile.get("strategy", "balanced")
    phase = profile.get("phase")
    relay_states = relay_states or {}
    timestamp = int(time.time() * 1000)

    # 1. Compute derived metrics and merge with raw readings
    derived = compute_derived(readings, history=history)
    all_metrics = {**readings, **derived}

    # Derive current hour for time window filtering
    current_hour: float | None = None
    if now is not None:
        current_hour = now.hour + now.minute / 60.0

    # 2. Score each goal
    goal_scores: dict[str, float] = {}
    for goal in goals:
        # Skip goals for other phases
        goal_phase = goal.get("phase")
        if goal_phase and phase and goal_phase != phase:
            continue

        # Skip goals outside their time window
        tw = goal.get("timeWindow")
        if tw and current_hour is not None:
            from simulations.state_planner import hour_in_window
            if not hour_in_window(current_hour, tw["onHour"], tw["offHour"]):
                continue

        metric = goal.get("metric", "")
        metric_type = goal.get("metricType", goal.get("metric_type", "sensor"))

        if metric_type == "relay_schedule":
            schedule = goal.get("schedule")
            if schedule and metric in relay_states:
                goal_scores[metric] = score_relay_schedule(
                    relay_states[metric], schedule, now=now,
                )
        elif metric in all_metrics:
            goal_scores[metric] = score_goal(
                value=all_metrics[metric],
                range_min=goal.get("rangeMin", goal.get("range_min")),
                range_max=goal.get("rangeMax", goal.get("range_max")),
                tolerance=goal.get("tolerance", 0.0),
                strategy=strategy,
            )

    # 3. Weighted average
    total = 0.0
    total_weight = 0.0
    for goal in goals:
        goal_phase = goal.get("phase")
        if goal_phase and phase and goal_phase != phase:
            continue
        metric = goal.get("metric", "")
        if metric in goal_scores:
            weight = goal.get("priority", 1.0)
            total += goal_scores[metric] * weight
            total_weight += weight

    score = total / total_weight if total_weight > 0 else 0.0

    # 4. Compute energy if data available
    energy = None
    if actuator_runtime:
        energy = compute_energy(
            actuator_runtime, actuator_watts or {},
        )
        # Energy penalty for efficiency strategy
        if strategy == "efficiency" and energy.total_wh > 0:
            energy_penalty = min(0.1, energy.total_wh * 0.001)
            score = max(0.0, score - energy_penalty)

    # 5. Build result
    out_of_range = [m for m, s in goal_scores.items() if s < 1.0]

    return EcosystemHealth(
        score=round(score, 4),
        goal_scores={k: round(v, 4) for k, v in goal_scores.items()},
        out_of_range=out_of_range,
        energy=energy,
        timestamp=timestamp,
    )
