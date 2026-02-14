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
OBSERVATION_LOOKBACK_MS = 7 * 24 * 60 * 60 * 1000  # 7 days

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

        # 2. Skip if insufficient outcome data
        total_outcomes = sum(rp.get("sample_count", 0) for rp in rule_performance)
        if total_outcomes < 5:
            logger.info(f"Rule advisor: insufficient outcome data ({total_outcomes} total), skipping")
            return []

        # 3. Build LLM prompt and get suggestions
        if not self._ollama or not self._ollama.is_available():
            logger.warning("Rule advisor: Ollama unavailable, skipping")
            return []

        prompt = self._build_analysis_prompt(rule_performance, baseline_summary, observation_summary)
        try:
            raw = self._ollama.generate(prompt, system=ADVISOR_SYSTEM_PROMPT, format="json")
            suggestions = self._parse_suggestions(raw)
        except Exception as e:
            logger.error(f"Rule advisor LLM call failed: {e}")
            return []

        if not suggestions:
            logger.info("Rule advisor: no suggestions from LLM")
            return []

        # 4. Store suggestions, auto-apply high-confidence threshold tweaks
        stored: list[dict] = []
        for s in suggestions:
            # Look up current value from in-memory rules
            current_value = self._get_rule_field_value(s["rule_name"], s["field"])
            if current_value is None:
                logger.warning(f"Rule advisor: rule '{s['rule_name']}' or field '{s['field']}' not found, skipping")
                continue

            suggestion_id = str(uuid.uuid4())
            record = self._sqlite.insert_suggestion(
                id=suggestion_id,
                rule_name=s["rule_name"],
                field=s["field"],
                current_value=json.dumps(current_value),
                suggested_value=json.dumps(s["suggested_value"]),
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
            sensor_unit = "°C" if "temp" in cond["sensor"] else "%"
            rules_section += (
                f"- {rp['rule_name']} "
                f"(sensor: {cond['sensor']}, operator: {cond['operator']}, "
                f"threshold: {cond['threshold']}{sensor_unit}, "
                f"duration: {cond['duration_seconds']}s): "
                f"{rp['sample_count']} outcomes, "
                f"avg effectiveness: {rp['avg_effectiveness']:+.2f}, "
                f"success rate: {rp['success_rate']:.0%}\n"
            )
            if "forecast" in cond:
                rules_section += (
                    f"  Forecast: {cond['forecast']} {cond.get('forecast_threshold', '?')}{sensor_unit} "
                    f"within {cond.get('forecast_within_minutes', '?')}min\n"
                )
            if "baseline_deviation" in cond:
                rules_section += f"  Baseline deviation trigger: {cond['baseline_deviation']}σ\n"

        # Format baselines
        baseline_section = ""
        if baseline_summary:
            baseline_section = "\nLearned baselines (typical values by hour):\n"
            for b in baseline_summary:
                unit = "°C" if b["metric"] == "temperature" else "%"
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
        """Modify a rule's condition field in-memory."""
        for rule in self._engine.rules:
            if rule.name == rule_name:
                if hasattr(rule.condition, field):
                    setattr(rule.condition, field, value)
                    logger.info(f"Applied rule change: {rule_name}.{field} = {value}")
                return
