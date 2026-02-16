"""
Rule Advisor — analyzes outcome data to suggest rule improvements.

Phase 4 of the cybernetic loop. Periodically reviews:
- Rule effectiveness (from cortex_outcomes)
- Learned baselines (from cortex_baselines)
- Human observations (from events table)

Produces threshold/timing adjustment suggestions. High-confidence
threshold changes auto-apply to in-memory rules; others require approval.
"""

import json
import logging
import math
import time
import uuid
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .sqlite_client import SqliteClient
    from .outcome_tracker import OutcomeTracker
    from .cortex_memory import CortexMemory
    from .ollama_client import OllamaClient
    from .decision_engine import DecisionEngine

logger = logging.getLogger(__name__)

AUTO_APPLY_CONFIDENCE = 0.8
AUTO_APPLY_FIELDS = {"threshold", "forecast_threshold"}

# Effectiveness guard — skip "unreachable" suggestions for rules that are
# already working well.  If a rule has high effectiveness, the threshold
# appears unreachable only because the rule keeps the environment controlled.
EFFECTIVENESS_GUARD_THRESHOLD = 0.5
EFFECTIVENESS_GUARD_MIN_SAMPLES = 3
TOO_SENSITIVE_MAX_CHANGE_PCT = 0.25  # Cap adjustment to ±25% of original
OBSERVATION_LOOKBACK_MS = 7 * 24 * 60 * 60 * 1000  # 7 days
from .sensor_meta import guess_sensor_type, sensor_unit as get_sensor_unit

ADVISOR_SYSTEM_PROMPT = """You are analyzing automation rule performance for an IoT sensor monitoring system.
You will receive rule definitions with their recent outcome data, learned sensor baselines, and human observations.

Your job is to suggest specific adjustments to improve rule effectiveness. Only suggest changes with clear data support.

Respond with valid JSON only in this format:
{
  "suggestions": [
    {
      "rule_name": "exact_rule_name",
      "field": "threshold",
      "suggested_value": 27,
      "reason": "Brief data-driven explanation",
      "confidence": 0.85
    }
  ]
}

Rules for suggestions:
- field must be one of: threshold, duration_seconds, forecast_threshold, forecast_within_minutes, baseline_deviation
- suggested_value must be a number
- confidence must be between 0.0 and 1.0
- Only suggest changes where outcome data shows clear room for improvement
- If all rules are performing well (success_rate > 80%), return {"suggestions": []}
- Never suggest disabling a rule — only adjust parameters"""


class RuleAdvisor:
    """Analyzes outcome data and suggests rule improvements."""

    def __init__(
        self,
        sqlite: "SqliteClient",
        outcome_tracker: "OutcomeTracker",
        memory: "CortexMemory",
        ollama: "OllamaClient",
        engine: "DecisionEngine",
    ):
        self._sqlite = sqlite
        self._outcomes = outcome_tracker
        self._memory = memory
        self._ollama = ollama
        self._engine = engine
        self._last_run_ts: int | None = None
        self._ensure_tables()

    @property
    def last_run_ts(self) -> int | None:
        return self._last_run_ts

    def _ensure_tables(self) -> None:
        """Create suggestions table if it doesn't exist."""
        db = self._sqlite._get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS cortex_suggestions (
                id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                rule_name TEXT NOT NULL,
                field TEXT NOT NULL,
                current_value TEXT NOT NULL,
                suggested_value TEXT NOT NULL,
                reason TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                resolved_at INTEGER,
                outcome_sample_count INTEGER,
                observation_context TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_suggestions_status
                ON cortex_suggestions(status);
            CREATE INDEX IF NOT EXISTS idx_suggestions_rule
                ON cortex_suggestions(rule_name);
        """)
        db.commit()

    def analyze(self) -> list[dict]:
        """Run a full analysis cycle. Returns new suggestions."""
        self._last_run_ts = int(time.time() * 1000)

        # 1. Gather data
        rule_performance = self._collect_rule_performance()
        baseline_summary = self._collect_baseline_summary()
        observation_summary = self._collect_observation_summary()

        # 2. Deterministic pre-pass (no LLM needed)
        deterministic = self._deterministic_gap_analysis(baseline_summary, rule_performance)

        # 3. LLM path (gated on outcome count >= 5 and Ollama availability)
        total_outcomes = sum(rp.get("sample_count", 0) for rp in rule_performance)
        llm_suggestions: list[dict] = []

        if total_outcomes >= 5 and self._ollama and self._ollama.is_available():
            prompt = self._build_analysis_prompt(rule_performance, baseline_summary, observation_summary)
            try:
                raw = self._ollama.generate(prompt, system=ADVISOR_SYSTEM_PROMPT, format="json")
                llm_suggestions = self._parse_suggestions(raw)
            except Exception as e:
                logger.error(f"Rule advisor LLM call failed: {e}")

        # 4. Merge deterministic + LLM suggestions
        all_suggestions = self._merge_suggestions(deterministic, llm_suggestions)

        if not all_suggestions:
            logger.info("Rule advisor: no suggestions")
            return []

        # 5. Store suggestions, auto-apply high-confidence threshold tweaks
        stored: list[dict] = []
        for s in all_suggestions:
            # Look up current value from in-memory rules
            current_value = self._get_rule_field_value(s["rule_name"], s["field"])
            if current_value is None:
                logger.warning(f"Rule advisor: rule '{s['rule_name']}' or field '{s['field']}' not found, skipping")
                continue

            # Skip no-op suggestions where the value wouldn't actually change
            # Use rounded comparison to catch floating-point near-matches (e.g., 20.67 vs 20.670000001)
            if round(current_value, 4) == round(s["suggested_value"], 4):
                logger.debug(f"Rule advisor: skipping no-op suggestion for {s['rule_name']}.{s['field']} ({current_value} → {s['suggested_value']})")
                continue

            # Skip duplicate suggestions already applied or pending
            suggested_json = json.dumps(s["suggested_value"])
            if self._sqlite.has_duplicate_suggestion(s["rule_name"], s["field"], suggested_json):
                logger.debug(f"Rule advisor: skipping duplicate suggestion for {s['rule_name']}.{s['field']} → {s['suggested_value']}")
                continue

            suggestion_id = str(uuid.uuid4())
            record = self._sqlite.insert_suggestion(
                id=suggestion_id,
                rule_name=s["rule_name"],
                field=s["field"],
                current_value=json.dumps(current_value),
                suggested_value=suggested_json,
                reason=s["reason"],
                confidence=s["confidence"],
                outcome_sample_count=total_outcomes,
                observation_context=observation_summary if observation_summary else None,
            )
            stored.append(record)

            # Auto-apply high-confidence threshold adjustments
            if s["confidence"] >= AUTO_APPLY_CONFIDENCE and s["field"] in AUTO_APPLY_FIELDS:
                self._apply_to_engine(s["rule_name"], s["field"], s["suggested_value"])
                self._sqlite.update_suggestion_status(suggestion_id, "applied")
                record["status"] = "applied"
                logger.info(
                    f"Rule advisor: auto-applied {s['rule_name']}.{s['field']}: "
                    f"{current_value} → {s['suggested_value']} (confidence: {s['confidence']:.0%})"
                )

        return stored

    def apply_suggestion(self, suggestion_id: str) -> bool:
        """Manually approve and apply a suggestion."""
        suggestion = self._sqlite.get_suggestion(suggestion_id)
        if not suggestion or suggestion["status"] != "pending":
            return False

        rule_name = suggestion["ruleName"]
        field = suggestion["field"]
        new_value = json.loads(suggestion["suggestedValue"])

        self._apply_to_engine(rule_name, field, new_value)
        self._sqlite.update_suggestion_status(suggestion_id, "applied")
        logger.info(f"Rule advisor: manually applied suggestion {suggestion_id}")
        return True

    def reject_suggestion(self, suggestion_id: str) -> bool:
        """Reject a pending suggestion."""
        suggestion = self._sqlite.get_suggestion(suggestion_id)
        if not suggestion or suggestion["status"] != "pending":
            return False

        self._sqlite.update_suggestion_status(suggestion_id, "rejected")
        logger.info(f"Rule advisor: rejected suggestion {suggestion_id}")
        return True

    # ── Deterministic Gap Analysis ─────────────────────────────────────

    def _deterministic_gap_analysis(
        self,
        baseline_summary: list[dict],
        rule_performance: list[dict] | None = None,
    ) -> list[dict]:
        """Programmatically compare rule thresholds against learned baselines.

        Args:
            baseline_summary: Per-metric, per-hour baseline statistics.
            rule_performance: Optional per-rule effectiveness data.  When
                provided, rules with high effectiveness (>0.5 avg, >=3
                outcomes) are shielded from "unreachable" and "stale
                forecast" suggestions — their controlled baselines make
                thresholds appear unreachable, but that's the desired state.
        """
        suggestions: list[dict] = []
        if not baseline_summary:
            return suggestions

        # Build effectiveness lookup
        eff_by_rule: dict[str, dict] = {}
        if rule_performance:
            for rp in rule_performance:
                eff_by_rule[rp["rule_name"]] = rp

        # Group baselines by metric
        baselines_by_metric: dict[str, list[dict]] = {}
        for b in baseline_summary:
            baselines_by_metric.setdefault(b["metric"], []).append(b)

        for rule in self._engine.rules:
            if not rule.enabled:
                continue
            if rule.condition.scope != "self":
                continue

            metric = guess_sensor_type(rule.condition.sensor)
            if not metric or metric not in baselines_by_metric:
                continue

            hourly = baselines_by_metric[metric]
            if not hourly:
                continue

            total_samples = sum(b["sampleCount"] for b in hourly)
            if total_samples < 10:
                continue

            hours_covered = len(hourly)
            threshold = rule.condition.threshold
            op = rule.condition.operator

            # Effectiveness guard: skip unreachable/stale-forecast for rules
            # that are already working well.
            rule_eff = eff_by_rule.get(rule.name)
            rule_is_effective = (
                rule_eff is not None
                and rule_eff.get("sample_count", 0) >= EFFECTIVENESS_GUARD_MIN_SAMPLES
                and rule_eff.get("avg_effectiveness", 0) > EFFECTIVENESS_GUARD_THRESHOLD
            )

            # --- (a) Unreachable threshold ---
            if not rule_is_effective:
                unreachable = self._detect_unreachable(rule, hourly, op, threshold, total_samples, hours_covered)
                if unreachable:
                    suggestions.append(unreachable)

            # --- (b) Too-sensitive threshold ---
            if not rule_is_effective:
                sensitive = self._detect_too_sensitive(rule, hourly, op, threshold)
                if sensitive:
                    suggestions.append(sensitive)

            # --- (c) Missing baseline_deviation ---
            missing_bd = self._detect_missing_baseline_deviation(rule, hourly)
            if missing_bd:
                suggestions.append(missing_bd)

            # --- (d) Stale forecast_threshold ---
            if not rule_is_effective:
                stale_fc = self._detect_stale_forecast(rule, hourly, total_samples, hours_covered)
                if stale_fc:
                    suggestions.append(stale_fc)

        return suggestions

    def _detect_unreachable(
        self, rule, hourly: list[dict], op: str, threshold: float,
        total_samples: int, hours_covered: int,
    ) -> dict | None:
        """Threshold beyond avg ± 2σ for ALL hours → unreachable."""
        # Use a small epsilon to avoid re-triggering on thresholds that are
        # already very close to the boundary (e.g., from a previous adjustment)
        _EPS = 0.02

        if op in (">", ">="):
            upper_bounds = [b["avg"] + 2 * b["stdDev"] for b in hourly]
            max_upper = max(upper_bounds)
            if threshold > max_upper + _EPS:
                # Round DOWN so the suggested value is actually within the reachable range
                suggested = math.floor(max_upper * 100) / 100
                if round(suggested, 4) == round(threshold, 4):
                    return None
                confidence = min(1.0, total_samples / 500) * 0.6 + min(1.0, hours_covered / 24) * 0.4
                return {
                    "rule_name": rule.name,
                    "field": "threshold",
                    "suggested_value": suggested,
                    "reason": f"Threshold {threshold} is unreachable — max baseline upper bound is {max_upper:.2f}",
                    "confidence": round(confidence, 3),
                }
        elif op in ("<", "<="):
            lower_bounds = [b["avg"] - 2 * b["stdDev"] for b in hourly]
            min_lower = min(lower_bounds)
            if threshold < min_lower - _EPS:
                # Round UP so the suggested value is actually within the reachable range
                suggested = math.ceil(min_lower * 100) / 100
                if round(suggested, 4) == round(threshold, 4):
                    return None
                confidence = min(1.0, total_samples / 500) * 0.6 + min(1.0, hours_covered / 24) * 0.4
                return {
                    "rule_name": rule.name,
                    "field": "threshold",
                    "suggested_value": suggested,
                    "reason": f"Threshold {threshold} is unreachable — min baseline lower bound is {min_lower:.2f}",
                    "confidence": round(confidence, 3),
                }
        return None

    def _detect_too_sensitive(
        self, rule, hourly: list[dict], op: str, threshold: float,
    ) -> dict | None:
        """Threshold within 1σ of the mean for >50% of hours → too sensitive."""
        if op in (">", ">="):
            sensitive_hours = sum(1 for b in hourly if threshold <= b["avg"] + b["stdDev"])
        elif op in ("<", "<="):
            sensitive_hours = sum(1 for b in hourly if threshold >= b["avg"] - b["stdDev"])
        else:
            return None

        sensitive_ratio = sensitive_hours / len(hourly) if hourly else 0
        if sensitive_ratio <= 0.5:
            return None

        # Suggest sample-weighted mean of avg ± 2σ
        total_weight = sum(b["sampleCount"] for b in hourly)
        if total_weight == 0:
            return None

        if op in (">", ">="):
            weighted = sum((b["avg"] + 2 * b["stdDev"]) * b["sampleCount"] for b in hourly) / total_weight
        else:
            weighted = sum((b["avg"] - 2 * b["stdDev"]) * b["sampleCount"] for b in hourly) / total_weight

        suggested = round(weighted, 2)

        # Cap: don't move more than 25% from original threshold
        max_delta = abs(threshold) * TOO_SENSITIVE_MAX_CHANGE_PCT
        if max_delta > 0:
            suggested = max(threshold - max_delta, min(threshold + max_delta, suggested))
            suggested = round(suggested, 2)

        if suggested == threshold:
            return None

        return {
            "rule_name": rule.name,
            "field": "threshold",
            "suggested_value": suggested,
            "reason": f"Threshold {threshold} is within 1σ of the mean for {sensitive_ratio:.0%} of hours — fires too often",
            "confidence": round(0.7 * sensitive_ratio, 3),
        }

    def _detect_missing_baseline_deviation(
        self, rule, hourly: list[dict],
    ) -> dict | None:
        """Stable baselines (CV < 10%) with no baseline_deviation → suggest adding."""
        if rule.condition.baseline_deviation is not None:
            return None
        if rule.condition.threshold == 0:
            # Rules with threshold=0 are forecast-only or baseline-only
            return None

        hours_covered = len(hourly)
        if hours_covered < 12:
            return None

        # Coefficient of variation across all hours
        total_samples = sum(b["sampleCount"] for b in hourly)
        if total_samples == 0:
            return None

        weighted_avg = sum(b["avg"] * b["sampleCount"] for b in hourly) / total_samples
        if weighted_avg == 0:
            return None

        weighted_std = sum(b["stdDev"] * b["sampleCount"] for b in hourly) / total_samples
        cv = weighted_std / abs(weighted_avg)

        if cv >= 0.10:
            return None

        return {
            "rule_name": rule.name,
            "field": "baseline_deviation",
            "suggested_value": 2.0,
            "reason": f"Stable baselines (CV={cv:.1%}) across {hours_covered} hours — baseline deviation can catch anomalies",
            "confidence": 0.65,
        }

    def _detect_stale_forecast(
        self, rule, hourly: list[dict], total_samples: int, hours_covered: int,
    ) -> dict | None:
        """Forecast threshold unreachable → suggest adjustment."""
        if not rule.condition.forecast or rule.condition.forecast_threshold is None:
            return None

        fc_threshold = rule.condition.forecast_threshold
        forecast_type = rule.condition.forecast
        op = ">=" if forecast_type == "will_exceed" else "<="

        _EPS = 0.02

        if op in (">", ">="):
            upper_bounds = [b["avg"] + 2 * b["stdDev"] for b in hourly]
            max_upper = max(upper_bounds)
            if fc_threshold > max_upper + _EPS:
                suggested = math.floor(max_upper * 100) / 100
                if round(suggested, 4) == round(fc_threshold, 4):
                    return None
                confidence = min(1.0, total_samples / 500) * 0.6 + min(1.0, hours_covered / 24) * 0.4
                return {
                    "rule_name": rule.name,
                    "field": "forecast_threshold",
                    "suggested_value": suggested,
                    "reason": f"Forecast threshold {fc_threshold} is unreachable — max baseline upper bound is {max_upper:.2f}",
                    "confidence": round(confidence, 3),
                }
        elif op in ("<", "<="):
            lower_bounds = [b["avg"] - 2 * b["stdDev"] for b in hourly]
            min_lower = min(lower_bounds)
            if fc_threshold < min_lower - _EPS:
                suggested = math.ceil(min_lower * 100) / 100
                if round(suggested, 4) == round(fc_threshold, 4):
                    return None
                confidence = min(1.0, total_samples / 500) * 0.6 + min(1.0, hours_covered / 24) * 0.4
                return {
                    "rule_name": rule.name,
                    "field": "forecast_threshold",
                    "suggested_value": suggested,
                    "reason": f"Forecast threshold {fc_threshold} is unreachable — min baseline lower bound is {min_lower:.2f}",
                    "confidence": round(confidence, 3),
                }
        return None

    def _merge_suggestions(
        self, deterministic: list[dict], llm: list[dict],
    ) -> list[dict]:
        """Deduplicate by (rule_name, field). Higher confidence wins; ties within 0.1 prefer deterministic."""
        by_key: dict[tuple[str, str], dict] = {}
        source: dict[tuple[str, str], str] = {}

        for s in deterministic:
            key = (s["rule_name"], s["field"])
            by_key[key] = s
            source[key] = "deterministic"

        for s in llm:
            key = (s["rule_name"], s["field"])
            if key not in by_key:
                by_key[key] = s
                source[key] = "llm"
            else:
                existing = by_key[key]
                if s["confidence"] > existing["confidence"] + 0.1:
                    # LLM clearly more confident
                    by_key[key] = s
                    source[key] = "llm"
                # Otherwise keep deterministic (ties within 0.1 prefer deterministic)

        return list(by_key.values())

    # ── Data Collection ───────────────────────────────────────────────

    def _collect_rule_performance(self) -> list[dict]:
        """Collect outcome statistics per rule."""
        performance: list[dict] = []

        for rule in self._engine.rules:
            if not rule.enabled:
                continue

            # Query outcomes where the reason matches this rule's action reason
            outcomes = self._outcomes.query_outcomes(limit=50)
            # Filter by rule — match on action reason substring
            rule_outcomes = [
                o for o in outcomes
                if o.get("reason") and rule.action.reason in o["reason"]
            ]

            if not rule_outcomes:
                performance.append({
                    "rule_name": rule.name,
                    "description": rule.description,
                    "condition": {
                        "sensor": rule.condition.sensor,
                        "operator": rule.condition.operator,
                        "threshold": rule.condition.threshold,
                        "duration_seconds": rule.condition.duration_seconds,
                    },
                    "sample_count": 0,
                    "avg_effectiveness": 0.0,
                    "success_rate": 0.0,
                })
                continue

            scores = [o["effectiveness"] for o in rule_outcomes]
            avg_eff = sum(scores) / len(scores) if scores else 0.0
            success_count = sum(1 for s in scores if s > 0.1)
            success_rate = success_count / len(scores) if scores else 0.0

            perf = {
                "rule_name": rule.name,
                "description": rule.description,
                "condition": {
                    "sensor": rule.condition.sensor,
                    "operator": rule.condition.operator,
                    "threshold": rule.condition.threshold,
                    "duration_seconds": rule.condition.duration_seconds,
                },
                "sample_count": len(rule_outcomes),
                "avg_effectiveness": round(avg_eff, 3),
                "success_rate": round(success_rate, 3),
            }

            # Add forecast fields if present
            if rule.condition.forecast:
                perf["condition"]["forecast"] = rule.condition.forecast
                perf["condition"]["forecast_threshold"] = rule.condition.forecast_threshold
                perf["condition"]["forecast_within_minutes"] = rule.condition.forecast_within_minutes

            if rule.condition.baseline_deviation is not None:
                perf["condition"]["baseline_deviation"] = rule.condition.baseline_deviation

            # Phase 5: cross-device scope context
            if rule.condition.scope != "self":
                perf["condition"]["scope"] = rule.condition.scope
            if rule.action.target_scope != "self":
                perf["action_target_scope"] = rule.action.target_scope

            performance.append(perf)

        return performance

    def _collect_baseline_summary(self) -> list[dict]:
        """Collect current baseline summaries across devices."""
        summaries: list[dict] = []
        devices = self._sqlite.get_all_devices()

        for device in devices:
            baselines = self._memory.get_all_baselines(device.id)
            for b in baselines:
                if b["sampleCount"] >= 10:
                    summaries.append(b)

        return summaries

    def _collect_observation_summary(self) -> str | None:
        """Collect recent human observations grouped by category."""
        now = int(time.time() * 1000)
        since = now - OBSERVATION_LOOKBACK_MS

        events = self._sqlite.query_events(
            since_ms=since, event_type="observation", limit=100,
        )

        if not events:
            return None

        counts: dict[str, int] = {}
        for evt in events:
            category = evt.payload.get("category", "unknown") if evt.payload else "unknown"
            counts[category] = counts.get(category, 0) + 1

        parts = [f"{cat}: {n} occurrence(s)" for cat, n in sorted(counts.items(), key=lambda x: -x[1])]
        return "; ".join(parts)

    # ── LLM Prompt Building ───────────────────────────────────────────

    def _build_analysis_prompt(
        self,
        rule_performance: list[dict],
        baseline_summary: list[dict],
        observation_summary: str | None,
    ) -> str:
        # Format rule performance
        rules_section = "Current rules and their recent performance:\n"
        for rp in rule_performance:
            cond = rp["condition"]
            stype = guess_sensor_type(cond["sensor"])
            unit = get_sensor_unit(stype)
            rules_section += (
                f"- {rp['rule_name']} "
                f"(sensor: {cond['sensor']}, operator: {cond['operator']}, "
                f"threshold: {cond['threshold']}{unit}, "
                f"duration: {cond['duration_seconds']}s): "
                f"{rp['sample_count']} outcomes, "
                f"avg effectiveness: {rp['avg_effectiveness']:+.2f}, "
                f"success rate: {rp['success_rate']:.0%}\n"
            )
            if "forecast" in cond:
                rules_section += (
                    f"  Forecast: {cond['forecast']} {cond.get('forecast_threshold', '?')}{unit} "
                    f"within {cond.get('forecast_within_minutes', '?')}min\n"
                )
            if "baseline_deviation" in cond:
                rules_section += f"  Baseline deviation trigger: {cond['baseline_deviation']}σ\n"

        # Format baselines
        baseline_section = ""
        if baseline_summary:
            baseline_section = "\nLearned baselines (typical values by hour):\n"
            for b in baseline_summary:
                unit = get_sensor_unit(b["metric"])
                baseline_section += (
                    f"- {b['metric']} hour {b['hour']}: "
                    f"avg={b['avg']}{unit}, std_dev={b['stdDev']} "
                    f"({b['sampleCount']} samples)\n"
                )

        # Format observations
        obs_section = ""
        if observation_summary:
            obs_section = f"\nRecent human observations (last 7 days):\n{observation_summary}\n"

        return f"""{rules_section}{baseline_section}{obs_section}
Analyze the above data and suggest specific parameter adjustments to improve rule effectiveness.
Consider whether thresholds are too sensitive or not sensitive enough based on outcome data and baselines."""

    # ── Response Parsing ──────────────────────────────────────────────

    def _parse_suggestions(self, raw: str) -> list[dict]:
        """Parse LLM response into validated suggestion dicts."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"Rule advisor: failed to parse LLM response as JSON: {raw[:200]}")
            return []

        raw_suggestions = data.get("suggestions", [])
        if not isinstance(raw_suggestions, list):
            return []

        valid: list[dict] = []
        valid_fields = {"threshold", "duration_seconds", "forecast_threshold", "forecast_within_minutes", "baseline_deviation"}

        for s in raw_suggestions:
            if not isinstance(s, dict):
                continue

            rule_name = s.get("rule_name")
            field = s.get("field")
            suggested_value = s.get("suggested_value")
            reason = s.get("reason", "")
            confidence = s.get("confidence", 0.0)

            # Validate required fields
            if not rule_name or not field or suggested_value is None:
                continue
            if field not in valid_fields:
                continue
            if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                continue
            if not isinstance(suggested_value, (int, float)):
                continue

            # Verify rule exists
            if not any(r.name == rule_name for r in self._engine.rules):
                logger.warning(f"Rule advisor: LLM suggested unknown rule '{rule_name}', skipping")
                continue

            valid.append({
                "rule_name": rule_name,
                "field": field,
                "suggested_value": suggested_value,
                "reason": reason,
                "confidence": float(confidence),
            })

        return valid

    # ── Rule Modification ─────────────────────────────────────────────

    def _get_rule_field_value(self, rule_name: str, field: str) -> Any:
        """Get current value of a rule's field."""
        for rule in self._engine.rules:
            if rule.name == rule_name:
                return getattr(rule.condition, field, None)
        return None

    def _apply_to_engine(self, rule_name: str, field: str, value: Any) -> None:
        """Modify a rule's condition field in-memory and persist to DB."""
        for rule in self._engine.rules:
            if rule.name == rule_name:
                if hasattr(rule.condition, field):
                    setattr(rule.condition, field, value)
                    # Persist to DB if rule has an id
                    if rule.id:
                        self._sqlite.update_rule_condition_field(rule.id, field, value)
                    logger.info(f"Applied rule change: {rule_name}.{field} = {value}")
                return
