"""Simulation runner — time-stepping loop with real DecisionEngine integration.

Steps the physics environment forward, builds TelemetryMessages, evaluates
rules via the real DecisionEngine, applies resulting commands back to the
environment, and records all state for visualization.

Extended with outcome tracking, baseline learning, and adaptive rule
improvement via RuleAdvisor deterministic gap analysis.
"""

import copy
import logging
import math
import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import patch

from src.models.telemetry import TelemetryMessage, Reading
from src.services.analysis import build_trend_context, calculate_rate_of_change
from src.services.decision_engine import DecisionEngine, _rules_from_dicts
from src.services.forecaster import linear_forecast
from src.services.outcome_tracker import (
    SCALE_FACTORS, INTERVAL_WEIGHTS, OutcomeTracker,
)
from src.services.rule_advisor import AUTO_APPLY_CONFIDENCE
from src.services.sensor_meta import guess_sensor_type

from .environment import GrowTentEnvironment

logger = logging.getLogger(__name__)

# How many minutes of readings to keep for trend/forecast context
CONTEXT_WINDOW_MINUTES = 30

# Outcome check intervals in simulation steps (at 30s/step):
# 2 steps ≈ 1min, 10 steps ≈ 5min, 20 steps ≈ 10min
OUTCOME_CHECK_STEPS = {2: 60, 10: 300, 20: 600}


def _build_rule_performance_from_outcomes(outcomes: list) -> list[dict]:
    """Build rule_performance dicts from simulation OutcomeScore objects.

    Compatible with the format expected by
    ``RuleAdvisor._deterministic_gap_analysis()``.
    """
    by_rule: dict[str, list[float]] = {}
    for o in outcomes:
        by_rule.setdefault(o.rule_name, []).append(o.effectiveness)
    return [
        {
            "rule_name": name,
            "sample_count": len(scores),
            "avg_effectiveness": round(sum(scores) / len(scores), 3) if scores else 0.0,
            "success_rate": round(sum(1 for s in scores if s > 0.1) / len(scores), 3) if scores else 0.0,
        }
        for name, scores in by_rule.items()
    ]


@dataclass
class SimulationEvent:
    """A command that fired during the simulation."""
    time_minutes: float
    rule_name: str
    target: str
    value: Any
    reason: str


@dataclass
class OutcomeScore:
    """Scored outcome for a single command during simulation."""
    rule_name: str
    target: str
    reason: str
    time_minutes: float
    target_metric: str
    desired_direction: str
    pre_value: float
    post_values: dict[int, float]   # {60: val, 300: val, 600: val}
    effectiveness: float


@dataclass
class SimulationResult:
    """Complete output of a simulation run."""
    timestamps: list[float] = field(default_factory=list)  # elapsed minutes
    readings: dict[str, list[float]] = field(default_factory=dict)
    actuators: dict[str, list[bool]] = field(default_factory=dict)
    events: list[SimulationEvent] = field(default_factory=list)
    duration_minutes: int = 0
    num_rules: int = 0
    # Adaptive learning fields
    outcomes: list[OutcomeScore] = field(default_factory=list)
    baselines: dict[str, dict[int, dict]] = field(default_factory=dict)  # {sensor: {hour: {avg, std, n}}}


@dataclass
class AdaptiveResult:
    """Comparison output from a multi-phase adaptive simulation."""
    phase1: SimulationResult
    phase2: SimulationResult
    suggestions: list[dict]
    phase1_avg_effectiveness: float
    phase2_avg_effectiveness: float
    improvement: float


class SimulationRunner:
    """Runs a time-stepping simulation with a real DecisionEngine."""

    DEVICE_ID = "sim-grow-tent"
    LOCATION = "grow-tent"

    def __init__(
        self,
        environment: GrowTentEnvironment,
        rules: list[dict],
        duration_minutes: int = 180,
        step_seconds: int = 30,
        start_hour: int = 8,
        track_outcomes: bool = False,
    ) -> None:
        self.env = environment
        self.rules = rules
        self.duration_minutes = duration_minutes
        self.step_seconds = step_seconds
        self.start_hour = start_hour
        self.track_outcomes = track_outcomes

        # Build engine from rule dicts
        self.engine = DecisionEngine(rules=_rules_from_dicts(rules))

        # Accumulator for context building (rolling window)
        # {sensor_id: [(ts_ms, value), ...]}
        self._history: dict[str, list[tuple[int, float]]] = {}

        # Outcome tracking state (pending commands awaiting scoring)
        self._pending_outcomes: list[dict] = []

        # Baseline accumulator: {sensor_type: {hour: {sum, sum_sq, count}}}
        self._baselines: dict[str, dict[int, dict]] = {}

        # Proxy result for multi-day mode to capture outcomes from _check_pending_outcomes
        self._sim_result_proxy = SimulationResult()

    def run(self) -> SimulationResult:
        """Execute the full simulation and return results."""
        total_steps = (self.duration_minutes * 60) // self.step_seconds

        result = SimulationResult(
            duration_minutes=self.duration_minutes,
            num_rules=len(self.rules),
        )

        # Initialize reading/actuator timeseries keys from first snapshot
        initial_readings = self.env.get_readings()
        for key in initial_readings:
            result.readings[key] = []
        for key in self.env.get_actuator_states():
            result.actuators[key] = []

        # Base timestamp: today at start_hour
        base_dt = datetime.now().replace(
            hour=self.start_hour, minute=0, second=0, microsecond=0
        )
        base_ts_ms = int(base_dt.timestamp() * 1000)

        for step in range(total_steps):
            elapsed_seconds = step * self.step_seconds
            elapsed_minutes = elapsed_seconds / 60.0
            current_ts_ms = base_ts_ms + (elapsed_seconds * 1000)

            # Compute simulated datetime for time-of-day rules
            sim_dt = datetime.fromtimestamp(current_ts_ms / 1000)

            # 1. Step physics (pass current hour for ambient schedule)
            current_hour = sim_dt.hour + sim_dt.minute / 60.0
            self.env.step(self.step_seconds, current_hour=current_hour)

            # 2. Build TelemetryMessage
            readings = self.env.get_readings()
            telemetry = TelemetryMessage(
                version=1,
                ts=int(current_ts_ms),
                device_id=self.DEVICE_ID,
                location=self.LOCATION,
                readings=[
                    Reading(id=sid, value=val)
                    for sid, val in readings.items()
                ],
            )

            # 3. Accumulate history for context window
            self._accumulate(readings, int(current_ts_ms))

            # 4. Update baselines (if tracking)
            if self.track_outcomes:
                self._update_baselines(readings, sim_dt.hour)

            # 5. Build context (trends + forecasts)
            context = self._build_context(readings)

            # 6. Evaluate rules with mocked time.time() and datetime.now()
            mock_now = current_ts_ms / 1000.0
            with (
                patch("src.services.decision_engine.time.time", return_value=mock_now),
                patch("src.services.decision_engine.datetime") as mock_dt,
            ):
                mock_dt.now.return_value = sim_dt
                commands = self.engine.evaluate(telemetry, context)

            # 7. Check pending outcomes at this step
            if self.track_outcomes:
                self._check_pending_outcomes(step, readings, result)

            # 8. Apply commands to environment (skip redundant state changes)
            current_actuator_states = self.env.get_actuator_states()
            for cmd in commands:
                from .environment import ACTUATOR_MAP
                attr = ACTUATOR_MAP.get(cmd.target, cmd.target)
                already = current_actuator_states.get(attr)
                if already == bool(cmd.value):
                    continue  # actuator already in target state

                self.env.set_actuator(cmd.target, cmd.value)
                current_actuator_states = self.env.get_actuator_states()

                # Find which rule produced this command
                rule_name = cmd.reason or "unknown"
                for rule in self.engine.rules:
                    if (rule.action.target == cmd.target
                            and rule.action.value == cmd.value):
                        rule_name = rule.name
                        break

                result.events.append(SimulationEvent(
                    time_minutes=elapsed_minutes,
                    rule_name=rule_name,
                    target=cmd.target,
                    value=cmd.value,
                    reason=cmd.reason or "",
                ))
                logger.info(
                    f"[{elapsed_minutes:.1f}m] {rule_name}: "
                    f"{cmd.target}={'ON' if cmd.value else 'OFF'}"
                )

                # Track outcome for this command
                if self.track_outcomes:
                    self._track_command(step, elapsed_minutes, rule_name,
                                        cmd, readings)

            # 9. Record snapshot
            result.timestamps.append(elapsed_minutes)
            for key, val in readings.items():
                result.readings[key].append(val)
            for key, val in self.env.get_actuator_states().items():
                result.actuators[key].append(val)

        # Finalize any remaining pending outcomes
        if self.track_outcomes:
            self._finalize_pending_outcomes(result)
            result.baselines = self._get_baseline_summary()

        return result

    # ── Outcome Tracking ─────────────────────────────────────────────

    def _track_command(
        self, step: int, time_minutes: float, rule_name: str,
        cmd: Any, readings: dict[str, float],
    ) -> None:
        """Start tracking a command's outcome by snapshotting current state."""
        target_metric = OutcomeTracker._infer_target_metric(cmd.reason)
        desired_direction = OutcomeTracker._infer_desired_direction(
            cmd.reason, cmd.value, target_metric,
        )

        # Find the sensor matching this metric
        sensor_id = None
        pre_value = None
        for sid, val in readings.items():
            if guess_sensor_type(sid) == target_metric:
                sensor_id = sid
                pre_value = val
                break

        if sensor_id is None or pre_value is None:
            return

        self._pending_outcomes.append({
            "step": step,
            "time_minutes": time_minutes,
            "rule_name": rule_name,
            "target": cmd.target,
            "reason": cmd.reason or "",
            "target_metric": target_metric,
            "desired_direction": desired_direction,
            "sensor_id": sensor_id,
            "pre_value": pre_value,
            "post_values": {},  # {interval_seconds: value}
        })

    def _check_pending_outcomes(
        self, current_step: int, readings: dict[str, float],
        result: SimulationResult,
    ) -> None:
        """Check pending outcomes at configured step intervals."""
        completed = []
        for i, pending in enumerate(self._pending_outcomes):
            steps_elapsed = current_step - pending["step"]

            for check_steps, interval_secs in OUTCOME_CHECK_STEPS.items():
                if steps_elapsed == check_steps and interval_secs not in pending["post_values"]:
                    val = readings.get(pending["sensor_id"])
                    if val is not None:
                        pending["post_values"][interval_secs] = val

            # All intervals collected?
            if len(pending["post_values"]) >= len(OUTCOME_CHECK_STEPS):
                score = self._score_outcome(pending)
                result.outcomes.append(score)
                completed.append(i)

        # Remove completed (reverse order to maintain indices)
        for i in reversed(completed):
            self._pending_outcomes.pop(i)

    def _finalize_pending_outcomes(self, result: SimulationResult) -> None:
        """Score any remaining pending outcomes with whatever data we have."""
        for pending in self._pending_outcomes:
            if pending["post_values"]:
                score = self._score_outcome(pending)
                result.outcomes.append(score)
        self._pending_outcomes.clear()

    @staticmethod
    def _score_outcome(pending: dict) -> OutcomeScore:
        """Score a command outcome using the real OutcomeTracker algorithm."""
        scale = SCALE_FACTORS.get(pending["target_metric"], 2.0)
        effectiveness = OutcomeTracker._score_outcome(
            pending["pre_value"],
            pending["post_values"],
            pending["desired_direction"],
            scale,
        )
        return OutcomeScore(
            rule_name=pending["rule_name"],
            target=pending["target"],
            reason=pending["reason"],
            time_minutes=pending["time_minutes"],
            target_metric=pending["target_metric"],
            desired_direction=pending["desired_direction"],
            pre_value=pending["pre_value"],
            post_values=pending["post_values"],
            effectiveness=effectiveness,
        )

    # ── Baseline Learning ────────────────────────────────────────────

    def _update_baselines(
        self, readings: dict[str, float], hour: int,
    ) -> None:
        """Incrementally update baselines using Welford's algorithm."""
        for sensor_id, value in readings.items():
            sensor_type = guess_sensor_type(sensor_id)
            if sensor_type not in self._baselines:
                self._baselines[sensor_type] = {}
            if hour not in self._baselines[sensor_type]:
                self._baselines[sensor_type][hour] = {
                    "sum": 0.0, "sum_sq": 0.0, "count": 0,
                }
            b = self._baselines[sensor_type][hour]
            b["count"] += 1
            b["sum"] += value
            b["sum_sq"] += value * value

    def _get_baseline_summary(self) -> dict[str, dict[int, dict]]:
        """Convert raw baseline accumulators to {sensor: {hour: {avg, stdDev, sampleCount}}}."""
        summary: dict[str, dict[int, dict]] = {}
        for sensor_type, hours in self._baselines.items():
            summary[sensor_type] = {}
            for hour, b in hours.items():
                avg = b["sum"] / b["count"] if b["count"] > 0 else 0.0
                variance = (b["sum_sq"] / b["count"]) - (avg * avg) if b["count"] > 1 else 0.0
                std_dev = math.sqrt(max(0, variance))
                summary[sensor_type][hour] = {
                    "avg": round(avg, 2),
                    "stdDev": round(std_dev, 3),
                    "sampleCount": b["count"],
                }
        return summary

    def get_baselines_for_advisor(self) -> list[dict]:
        """Return baselines in the format RuleAdvisor._deterministic_gap_analysis() expects."""
        result = []
        for sensor_type, hours in self._get_baseline_summary().items():
            for hour, data in hours.items():
                if data["sampleCount"] >= 10:
                    result.append({
                        "deviceId": self.DEVICE_ID,
                        "metric": sensor_type,
                        "hour": hour,
                        "avg": data["avg"],
                        "stdDev": data["stdDev"],
                        "sampleCount": data["sampleCount"],
                    })
        return result

    def _accumulate(self, readings: dict[str, float], ts_ms: int) -> None:
        """Add readings to rolling history window."""
        cutoff = ts_ms - (CONTEXT_WINDOW_MINUTES * 60 * 1000)
        for sensor_id, value in readings.items():
            if sensor_id not in self._history:
                self._history[sensor_id] = []
            self._history[sensor_id].append((ts_ms, value))
            # Trim to context window
            self._history[sensor_id] = [
                (t, v) for t, v in self._history[sensor_id] if t >= cutoff
            ]

    def _build_context(self, current_readings: dict[str, float]) -> dict[str, Any]:
        """Build trend and forecast context from accumulated history."""
        trends: dict[str, Any] = {}
        forecasts: dict[str, Any] = {}

        for sensor_id, history in self._history.items():
            if len(history) < 3:
                continue

            timestamps = [t for t, _ in history]
            values = [v for _, v in history]

            # Trends
            trend_ctx = build_trend_context(sensor_id, values, timestamps)
            if trend_ctx:
                trends[sensor_id] = {
                    "trend": trend_ctx.trend,
                    "rate": trend_ctx.rate_of_change,
                    "current": trend_ctx.current_value,
                    "mean": trend_ctx.mean_30m,
                }

            # Forecasts
            rate = calculate_rate_of_change(values, timestamps)
            current = values[-1]
            pred_10 = linear_forecast(values, timestamps, 10.0)
            pred_15 = linear_forecast(values, timestamps, 15.0)
            forecasts[sensor_id] = {
                "rate": rate,
                "current": current,
                "predicted_10m": pred_10,
                "predicted_15m": pred_15,
            }

        return {"trends": trends, "forecasts": forecasts}


def run_adaptive(
    rules: list[dict],
    duration_minutes: int = 180,
    step_seconds: int = 30,
    start_hour: int = 8,
    ambient_temp: float = 32.0,
) -> AdaptiveResult:
    """Run a two-phase adaptive simulation proving learning improvement.

    Phase 1: Run with original rules, collect outcomes + baselines.
    Analysis: Use RuleAdvisor's deterministic gap analysis on learned baselines
              to suggest threshold/timing adjustments.
    Phase 2: Run with adjusted rules and compare effectiveness.

    Returns an AdaptiveResult with before/after comparison.
    """
    # ── Phase 1: Original rules ──────────────────────────────────────
    logger.info("=== Phase 1: Running with original rules ===")
    env1 = GrowTentEnvironment(noise=False, ambient_temp=ambient_temp)
    runner1 = SimulationRunner(
        environment=env1,
        rules=rules,
        duration_minutes=duration_minutes,
        step_seconds=step_seconds,
        start_hour=start_hour,
        track_outcomes=True,
    )
    result1 = runner1.run()

    phase1_avg = _avg_effectiveness(result1.outcomes)
    logger.info(
        f"Phase 1 complete: {len(result1.outcomes)} outcomes, "
        f"avg effectiveness: {phase1_avg:+.3f}"
    )

    # ── Analysis: Deterministic gap analysis ──────────────────────────
    baselines = runner1.get_baselines_for_advisor()
    logger.info(f"Learned {len(baselines)} baseline entries")

    # Instantiate a RuleAdvisor with the engine and mock dependencies.
    # Only _deterministic_gap_analysis is called — it needs self._engine only.
    from unittest.mock import MagicMock
    from src.services.rule_advisor import RuleAdvisor

    advisor = RuleAdvisor.__new__(RuleAdvisor)
    advisor._engine = runner1.engine
    advisor._sqlite = MagicMock()
    advisor._outcomes = MagicMock()
    advisor._memory = MagicMock()
    advisor._ollama = MagicMock()
    advisor._last_run_ts = None

    rule_performance = _build_rule_performance_from_outcomes(result1.outcomes)
    suggestions = advisor._deterministic_gap_analysis(baselines, rule_performance)
    logger.info(f"Gap analysis produced {len(suggestions)} suggestions")
    for s in suggestions:
        logger.info(
            f"  {s['rule_name']}.{s['field']}: → {s['suggested_value']} "
            f"(confidence: {s['confidence']:.0%}) — {s['reason']}"
        )

    # ── Phase 2: Apply suggestions and re-run ────────────────────────
    adjusted_rules = copy.deepcopy(rules)

    # Build a lookup for fast rule adjustment (only high-confidence)
    for suggestion in suggestions:
        if suggestion.get("confidence", 0) < AUTO_APPLY_CONFIDENCE:
            continue  # Skip low-confidence suggestions
        for rule_dict in adjusted_rules:
            if rule_dict["name"] == suggestion["rule_name"]:
                if suggestion["field"] in rule_dict.get("condition", {}):
                    old_val = rule_dict["condition"][suggestion["field"]]
                    rule_dict["condition"][suggestion["field"]] = suggestion["suggested_value"]
                    logger.info(
                        f"Adjusted {suggestion['rule_name']}.{suggestion['field']}: "
                        f"{old_val} → {suggestion['suggested_value']}"
                    )

    logger.info("=== Phase 2: Running with adjusted rules ===")
    env2 = GrowTentEnvironment(noise=False, ambient_temp=ambient_temp)
    runner2 = SimulationRunner(
        environment=env2,
        rules=adjusted_rules,
        duration_minutes=duration_minutes,
        step_seconds=step_seconds,
        start_hour=start_hour,
        track_outcomes=True,
    )
    result2 = runner2.run()

    phase2_avg = _avg_effectiveness(result2.outcomes)
    improvement = phase2_avg - phase1_avg
    logger.info(
        f"Phase 2 complete: {len(result2.outcomes)} outcomes, "
        f"avg effectiveness: {phase2_avg:+.3f} "
        f"(improvement: {improvement:+.3f})"
    )

    return AdaptiveResult(
        phase1=result1,
        phase2=result2,
        suggestions=suggestions,
        phase1_avg_effectiveness=phase1_avg,
        phase2_avg_effectiveness=phase2_avg,
        improvement=improvement,
    )


def _avg_effectiveness(outcomes: list[OutcomeScore]) -> float:
    """Compute average effectiveness across all outcomes."""
    if not outcomes:
        return 0.0
    return sum(o.effectiveness for o in outcomes) / len(outcomes)


# ── Multi-Day Adaptive Simulation ─────────────────────────────────────


@dataclass
class PhaseResult:
    """Results from a single phase between advisor checkpoints."""
    phase_num: int
    start_minutes: float
    end_minutes: float
    outcomes: list[OutcomeScore]
    avg_effectiveness: float
    events: list[SimulationEvent]
    num_suggestions_generated: int
    num_suggestions_applied: int
    suggestions: list[dict]


@dataclass
class MultiDayResult:
    """Complete output of a multi-day simulation with periodic advisor analysis."""
    total_duration_minutes: int
    num_checkpoints: int
    phases: list[PhaseResult]
    # Full continuous timeseries
    timestamps: list[float]
    readings: dict[str, list[float]]
    actuators: dict[str, list[bool]]
    all_events: list[SimulationEvent]
    # Improvement trajectory
    effectiveness_trajectory: list[float]
    overall_improvement: float
    total_suggestions: int
    total_applied: int
    # Baselines at each checkpoint
    baseline_snapshots: list[dict[str, dict[int, dict]]]


def _create_advisor(engine: "DecisionEngine") -> "Any":
    """Create a RuleAdvisor wired to the engine for deterministic gap analysis."""
    from unittest.mock import MagicMock
    from src.services.rule_advisor import RuleAdvisor

    advisor = RuleAdvisor.__new__(RuleAdvisor)
    advisor._engine = engine
    advisor._sqlite = MagicMock()
    advisor._outcomes = MagicMock()
    advisor._memory = MagicMock()
    advisor._ollama = MagicMock()
    advisor._last_run_ts = None
    return advisor


def run_multi_day(
    rules: list[dict],
    total_duration_minutes: int = 4320,   # 72h = 3 days
    checkpoint_interval_minutes: int = 720,  # 12h
    step_seconds: int = 30,
    start_hour: int = 8,
    use_ambient_schedule: bool = True,
    filter_by_confidence: bool = True,
) -> MultiDayResult:
    """Run a continuous multi-day simulation with periodic Rule Advisor checkpoints.

    A single SimulationRunner runs continuously. At each checkpoint:
    1. Pending outcomes are finalized and scored
    2. Baselines are snapshotted
    3. RuleAdvisor runs deterministic gap analysis on accumulated baselines
    4. High-confidence suggestions are applied to the engine's rules in-place
    5. Engine duration/cooldown state is reset for the new phase

    The simulation continues until all phases are complete or the advisor
    produces zero suggestions for two consecutive checkpoints (convergence).
    """
    from .environment import GrowTentEnvironment, default_ambient_schedule

    # ── Setup ────────────────────────────────────────────────────────
    env = GrowTentEnvironment(
        noise=False,
        ambient_schedule=default_ambient_schedule if use_ambient_schedule else None,
    )
    runner = SimulationRunner(
        environment=env,
        rules=copy.deepcopy(rules),
        duration_minutes=total_duration_minutes,
        step_seconds=step_seconds,
        start_hour=start_hour,
        track_outcomes=True,
    )
    advisor = _create_advisor(runner.engine)

    total_steps = (total_duration_minutes * 60) // step_seconds
    checkpoint_steps = (checkpoint_interval_minutes * 60) // step_seconds

    # Base timestamp
    base_dt = datetime.now().replace(
        hour=start_hour, minute=0, second=0, microsecond=0
    )
    base_ts_ms = int(base_dt.timestamp() * 1000)

    # Initialize result containers
    initial_readings = runner.env.get_readings()
    all_timestamps: list[float] = []
    all_readings: dict[str, list[float]] = {k: [] for k in initial_readings}
    all_actuators: dict[str, list[bool]] = {k: [] for k in runner.env.get_actuator_states()}
    all_events: list[SimulationEvent] = []

    phases: list[PhaseResult] = []
    baseline_snapshots: list[dict] = []
    effectiveness_trajectory: list[float] = []

    phase_outcomes: list[OutcomeScore] = []
    phase_events: list[SimulationEvent] = []
    phase_start_minutes = 0.0
    current_phase = 0
    total_suggestions = 0
    total_applied = 0
    consecutive_empty = 0  # track convergence

    logger.info(
        f"=== Multi-day simulation: {total_duration_minutes}min "
        f"({total_duration_minutes / 60:.0f}h), "
        f"checkpoints every {checkpoint_interval_minutes}min "
        f"({checkpoint_interval_minutes / 60:.0f}h) ==="
    )

    # ── Main Loop ────────────────────────────────────────────────────
    for step in range(total_steps):
        elapsed_seconds = step * step_seconds
        elapsed_minutes = elapsed_seconds / 60.0
        current_ts_ms = base_ts_ms + (elapsed_seconds * 1000)
        sim_dt = datetime.fromtimestamp(current_ts_ms / 1000)

        # Step physics with current hour for ambient schedule
        current_hour = sim_dt.hour + sim_dt.minute / 60.0
        runner.env.step(step_seconds, current_hour=current_hour)

        # Build telemetry
        readings = runner.env.get_readings()
        telemetry = TelemetryMessage(
            version=1,
            ts=int(current_ts_ms),
            device_id=runner.DEVICE_ID,
            location=runner.LOCATION,
            readings=[
                Reading(id=sid, value=val)
                for sid, val in readings.items()
            ],
        )

        # Accumulate history and baselines
        runner._accumulate(readings, int(current_ts_ms))
        runner._update_baselines(readings, sim_dt.hour)

        # Build context and evaluate rules
        context = runner._build_context(readings)
        mock_now = current_ts_ms / 1000.0
        with (
            patch("src.services.decision_engine.time.time", return_value=mock_now),
            patch("src.services.decision_engine.datetime") as mock_dt_patch,
        ):
            mock_dt_patch.now.return_value = sim_dt
            commands = runner.engine.evaluate(telemetry, context)

        # Check pending outcomes
        runner._check_pending_outcomes(step, readings, runner._sim_result_proxy)

        # Apply commands
        current_actuator_states = runner.env.get_actuator_states()
        for cmd in commands:
            from .environment import ACTUATOR_MAP
            attr = ACTUATOR_MAP.get(cmd.target, cmd.target)
            already = current_actuator_states.get(attr)
            if already == bool(cmd.value):
                continue

            runner.env.set_actuator(cmd.target, cmd.value)
            current_actuator_states = runner.env.get_actuator_states()

            rule_name = cmd.reason or "unknown"
            for rule in runner.engine.rules:
                if rule.action.target == cmd.target and rule.action.value == cmd.value:
                    rule_name = rule.name
                    break

            event = SimulationEvent(
                time_minutes=elapsed_minutes,
                rule_name=rule_name,
                target=cmd.target,
                value=cmd.value,
                reason=cmd.reason or "",
            )
            all_events.append(event)
            phase_events.append(event)

            runner._track_command(step, elapsed_minutes, rule_name, cmd, readings)

        # Record snapshot
        all_timestamps.append(elapsed_minutes)
        for key, val in readings.items():
            all_readings[key].append(val)
        for key, val in runner.env.get_actuator_states().items():
            all_actuators[key].append(val)

        # ── Checkpoint? ──────────────────────────────────────────────
        steps_into_phase = step - (current_phase * checkpoint_steps)
        is_checkpoint = (step > 0 and steps_into_phase == checkpoint_steps - 1)
        is_last_step = (step == total_steps - 1)

        if is_checkpoint or is_last_step:
            # Finalize pending outcomes
            for pending in runner._pending_outcomes:
                if pending["post_values"]:
                    score = runner._score_outcome(pending)
                    phase_outcomes.append(score)
            runner._pending_outcomes.clear()

            # Move outcomes scored via _check_pending_outcomes into phase list
            proxy_outcomes = runner._sim_result_proxy.outcomes
            phase_outcomes.extend(proxy_outcomes)
            runner._sim_result_proxy.outcomes = []

            avg_eff = _avg_effectiveness(phase_outcomes)
            effectiveness_trajectory.append(avg_eff)

            # Snapshot baselines
            baseline_snapshot = runner._get_baseline_summary()
            baseline_snapshots.append(copy.deepcopy(baseline_snapshot))

            # Run advisor (with effectiveness guard)
            baselines_for_advisor = runner.get_baselines_for_advisor()
            rule_performance = _build_rule_performance_from_outcomes(phase_outcomes)
            suggestions = advisor._deterministic_gap_analysis(baselines_for_advisor, rule_performance)

            num_applied = 0
            for s in suggestions:
                if filter_by_confidence and s.get("confidence", 0) < AUTO_APPLY_CONFIDENCE:
                    continue  # Skip low-confidence suggestions
                # Apply to engine in-place
                for rule in runner.engine.rules:
                    if rule.name == s["rule_name"]:
                        if hasattr(rule.condition, s["field"]):
                            old_val = getattr(rule.condition, s["field"])
                            setattr(rule.condition, s["field"], s["suggested_value"])
                            num_applied += 1
                            logger.info(
                                f"  Checkpoint {current_phase}: "
                                f"{s['rule_name']}.{s['field']}: "
                                f"{old_val} → {s['suggested_value']} "
                                f"({s['reason']})"
                            )
                        break

            total_suggestions += len(suggestions)
            total_applied += num_applied

            phase = PhaseResult(
                phase_num=current_phase,
                start_minutes=phase_start_minutes,
                end_minutes=elapsed_minutes,
                outcomes=list(phase_outcomes),
                avg_effectiveness=avg_eff,
                events=list(phase_events),
                num_suggestions_generated=len(suggestions),
                num_suggestions_applied=num_applied,
                suggestions=suggestions,
            )
            phases.append(phase)

            logger.info(
                f"Phase {current_phase} complete "
                f"({phase_start_minutes:.0f}-{elapsed_minutes:.0f}min): "
                f"{len(phase_outcomes)} outcomes, "
                f"avg eff: {avg_eff:+.3f}, "
                f"{len(suggestions)} suggestions ({num_applied} applied)"
            )

            # Track convergence (based on applied, not generated — unapplied
            # suggestions don't change the system so they'd repeat forever)
            if num_applied == 0:
                consecutive_empty += 1
            else:
                consecutive_empty = 0

            # Reset for next phase
            phase_outcomes = []
            phase_events = []
            phase_start_minutes = elapsed_minutes
            current_phase += 1

            # Reset engine duration/cooldown state
            runner.engine.sensor_states.clear()

            # Stop early if converged (2 consecutive empty checkpoints)
            if consecutive_empty >= 2 and not is_last_step:
                remaining_steps = total_steps - step - 1
                remaining_min = remaining_steps * step_seconds / 60.0
                logger.info(
                    f"Converged after {current_phase} phases "
                    f"(2 consecutive checkpoints with 0 suggestions). "
                    f"Running remaining {remaining_min:.0f}min without checkpoints."
                )
                # Run the rest as one final phase without further checkpoints
                _run_remaining_steps(
                    runner, step + 1, total_steps, step_seconds, base_ts_ms,
                    all_timestamps, all_readings, all_actuators, all_events,
                    phase_outcomes, phase_events,
                )
                # Finalize last phase
                for pending in runner._pending_outcomes:
                    if pending["post_values"]:
                        phase_outcomes.append(runner._score_outcome(pending))
                runner._pending_outcomes.clear()
                proxy_outcomes = runner._sim_result_proxy.outcomes
                phase_outcomes.extend(proxy_outcomes)
                runner._sim_result_proxy.outcomes = []

                avg_eff = _avg_effectiveness(phase_outcomes)
                effectiveness_trajectory.append(avg_eff)
                baseline_snapshots.append(copy.deepcopy(runner._get_baseline_summary()))

                phases.append(PhaseResult(
                    phase_num=current_phase,
                    start_minutes=phase_start_minutes,
                    end_minutes=all_timestamps[-1],
                    outcomes=list(phase_outcomes),
                    avg_effectiveness=avg_eff,
                    events=list(phase_events),
                    num_suggestions_generated=0,
                    num_suggestions_applied=0,
                    suggestions=[],
                ))
                break

    overall = effectiveness_trajectory[-1] - effectiveness_trajectory[0] if len(effectiveness_trajectory) >= 2 else 0.0

    return MultiDayResult(
        total_duration_minutes=total_duration_minutes,
        num_checkpoints=len(phases),
        phases=phases,
        timestamps=all_timestamps,
        readings=all_readings,
        actuators=all_actuators,
        all_events=all_events,
        effectiveness_trajectory=effectiveness_trajectory,
        overall_improvement=overall,
        total_suggestions=total_suggestions,
        total_applied=total_applied,
        baseline_snapshots=baseline_snapshots,
    )


def _run_remaining_steps(
    runner: SimulationRunner,
    start_step: int,
    total_steps: int,
    step_seconds: int,
    base_ts_ms: int,
    all_timestamps: list[float],
    all_readings: dict[str, list[float]],
    all_actuators: dict[str, list[bool]],
    all_events: list[SimulationEvent],
    phase_outcomes: list[OutcomeScore],
    phase_events: list[SimulationEvent],
) -> None:
    """Run remaining steps after convergence without checkpoints."""
    for step in range(start_step, total_steps):
        elapsed_seconds = step * step_seconds
        elapsed_minutes = elapsed_seconds / 60.0
        current_ts_ms = base_ts_ms + (elapsed_seconds * 1000)
        sim_dt = datetime.fromtimestamp(current_ts_ms / 1000)

        current_hour = sim_dt.hour + sim_dt.minute / 60.0
        runner.env.step(step_seconds, current_hour=current_hour)

        readings = runner.env.get_readings()
        telemetry = TelemetryMessage(
            version=1,
            ts=int(current_ts_ms),
            device_id=runner.DEVICE_ID,
            location=runner.LOCATION,
            readings=[Reading(id=sid, value=val) for sid, val in readings.items()],
        )

        runner._accumulate(readings, int(current_ts_ms))
        runner._update_baselines(readings, sim_dt.hour)

        context = runner._build_context(readings)
        mock_now = current_ts_ms / 1000.0
        with (
            patch("src.services.decision_engine.time.time", return_value=mock_now),
            patch("src.services.decision_engine.datetime") as mock_dt_patch,
        ):
            mock_dt_patch.now.return_value = sim_dt
            commands = runner.engine.evaluate(telemetry, context)

        runner._check_pending_outcomes(step, readings, runner._sim_result_proxy)

        current_actuator_states = runner.env.get_actuator_states()
        for cmd in commands:
            from .environment import ACTUATOR_MAP
            attr = ACTUATOR_MAP.get(cmd.target, cmd.target)
            already = current_actuator_states.get(attr)
            if already == bool(cmd.value):
                continue

            runner.env.set_actuator(cmd.target, cmd.value)
            current_actuator_states = runner.env.get_actuator_states()

            rule_name = cmd.reason or "unknown"
            for rule in runner.engine.rules:
                if rule.action.target == cmd.target and rule.action.value == cmd.value:
                    rule_name = rule.name
                    break

            event = SimulationEvent(
                time_minutes=elapsed_minutes,
                rule_name=rule_name,
                target=cmd.target,
                value=cmd.value,
                reason=cmd.reason or "",
            )
            all_events.append(event)
            phase_events.append(event)

            runner._track_command(step, elapsed_minutes, rule_name, cmd, readings)

        all_timestamps.append(elapsed_minutes)
        for key, val in readings.items():
            all_readings[key].append(val)
        for key, val in runner.env.get_actuator_states().items():
            all_actuators[key].append(val)
