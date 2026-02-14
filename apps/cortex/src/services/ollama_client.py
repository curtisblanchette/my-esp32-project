import json
import logging
from typing import Any, AsyncGenerator, Callable, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor

import httpx

from ..config import OLLAMA_URL, OLLAMA_MODEL
from ..models.telemetry import TelemetryMessage
from ..models.command import Command

if TYPE_CHECKING:
    from .sqlite_client import SqliteClient
    from .websocket_server import WebSocketServer

logger = logging.getLogger(__name__)


# ── Chat Intent Types ────────────────────────────────────────────────

OllamaIntent = dict[str, Any]
# Shape varies by intent type:
# {"intent": "command", "deviceId": "...", "target": "...", "action": "...", "value": ..., "reply": "..."}
# {"intent": "query", "deviceId": "...", "sensor": "...", "reply": "..."}
# {"intent": "history", "deviceId": "...", "timeframe": "...", "category": "...", "reply": "...", "summary": "..."}
# {"intent": "analyze", "deviceId": "...", "timeframe": "...", "metric": "...", "reply": "...", "summary": "..."}
# {"intent": "none", "reply": "..."}

# Thread pool for non-blocking LLM calls
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ollama-")

SYSTEM_PROMPT = """You are an AI assistant controlling a smart home IoT system.
You receive sensor data and must decide what actions to take.

Available actuators:
- relay1: A switch that can be set to true (ON) or false (OFF)

Your response must be valid JSON in one of these formats:

If action needed:
{"action": "command", "target": "relay1", "value": true, "reason": "Brief explanation"}

If no action needed:
{"action": "none", "reason": "Brief explanation"}

Be conservative - only take action when clearly necessary.
Consider comfort, energy efficiency, and avoiding rapid state changes.
"""


class OllamaClient:
    """Client for interacting with Ollama LLM."""

    def __init__(self, base_url: str = OLLAMA_URL, model: str = OLLAMA_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=60.0)

    def is_available(self) -> bool:
        """Check if Ollama is available."""
        try:
            response = self._client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Ollama not available: {e}")
            return False

    def analyze(
        self,
        telemetry: TelemetryMessage,
        context: dict[str, Any],
        recent_commands: list[dict] | None = None,
    ) -> Command | None:
        """
        Analyze telemetry data and context using the LLM.
        Returns a Command if action is needed, None otherwise.
        """
        prompt = self._build_prompt(telemetry, context, recent_commands)

        try:
            response = self._client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "system": SYSTEM_PROMPT,
                    "stream": False,
                    "format": "json",
                },
            )
            response.raise_for_status()

            result = response.json()
            response_text = result.get("response", "")
            logger.debug(f"LLM response: {response_text}")

            return self._parse_response(response_text, telemetry)

        except httpx.HTTPError as e:
            logger.error(f"Ollama HTTP error: {e}")
            return None
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return None

    def _build_prompt(
        self,
        telemetry: TelemetryMessage,
        context: dict[str, Any],
        recent_commands: list[dict] | None = None,
    ) -> str:
        """Build the prompt for the LLM with trend and baseline data."""
        # Format current readings
        readings_str = "\n".join(
            f"  - {r.id}: {r.value}{' ' + r.unit if r.unit else ''}"
            for r in telemetry.readings
        )

        # Format trend data (Phase 1)
        trend_str = ""
        trends = context.pop("_trends", None) if context else None
        if trends:
            trend_str = "\nTrend analysis (last 30 minutes):"
            for sensor, data in trends.items():
                direction = data.get("trend", "stable")
                rate = data.get("rate", 0)
                mean = data.get("mean", 0)
                unit = "°C/min" if "temp" in sensor else "%/min"
                trend_str += f"\n  - {sensor}: {direction} ({rate:+.2f}{unit}), 30min avg: {mean:.1f}"

        # Format baseline data (Phase 1)
        baseline_str = ""
        baselines = context.pop("_baselines", None) if context else None
        if baselines:
            baseline_str = "\nBaseline comparison (vs normal for this hour):"
            for sensor, data in baselines.items():
                avg = data.get("avg", 0)
                deviation = data.get("deviation")
                samples = data.get("samples", 0)
                if deviation is not None:
                    status = "normal" if abs(deviation) < 1.5 else "unusual" if abs(deviation) < 2.5 else "abnormal"
                    baseline_str += f"\n  - {sensor}: baseline={avg:.1f}, deviation={deviation:+.1f}σ ({status}, {samples} samples)"

        # Format sensor state context
        context_str = ""
        if context:
            for device_id, sensors in context.items():
                if device_id.startswith("_"):
                    continue
                if isinstance(sensors, dict):
                    context_str += f"\nDevice {device_id}:"
                    for sensor, state in sensors.items():
                        if isinstance(state, dict) and state.get("last_value") is not None:
                            context_str += f"\n  - {sensor}: last={state['last_value']}, condition_active={state.get('condition_active', False)}"

        # Format recent commands
        commands_str = ""
        if recent_commands:
            commands_str = "\nRecent commands (last 5 minutes):"
            for cmd in recent_commands[-5:]:
                commands_str += f"\n  - {cmd.get('target')}: {cmd.get('action')}={cmd.get('value')} ({cmd.get('reason', 'no reason')})"

        prompt = f"""Current sensor readings from {telemetry.device_id} at {telemetry.location}:
{readings_str}
{trend_str}{baseline_str}{f"\n\nSensor states:{context_str}" if context_str else ""}{commands_str}

Based on these readings, should any action be taken? Consider:
1. Is the temperature comfortable (18-26°C is typical comfort range)?
2. Is humidity at a reasonable level (30-60% is typical)?
3. Are there any concerning trends or deviations from baseline?

Respond with JSON only."""

        return prompt

    def _parse_response(self, response_text: str, telemetry: TelemetryMessage) -> Command | None:
        """Parse the LLM response into a Command."""
        import json

        try:
            data = json.loads(response_text)

            if data.get("action") == "command":
                return Command(
                    device_id=telemetry.device_id,
                    location=telemetry.location,
                    target=data.get("target", "relay1"),
                    action="set",
                    value=data.get("value", False),
                    reason=f"[AI] {data.get('reason', 'LLM decision')}",
                )
            else:
                logger.debug(f"LLM decided no action: {data.get('reason', 'no reason')}")
                return None

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return None

    def analyze_async(
        self,
        telemetry: TelemetryMessage,
        context: dict[str, Any],
        recent_commands: list[dict] | None = None,
        callback: Callable[[Command | None], None] | None = None,
    ) -> None:
        """
        Non-blocking version of analyze.
        Submits the analysis to a thread pool and calls callback(command) when done.
        """
        def _run():
            try:
                command = self.analyze(telemetry, context, recent_commands)
                if callback:
                    callback(command)
            except Exception as e:
                logger.error(f"Async LLM analysis failed: {e}")
                if callback:
                    callback(None)

        _executor.submit(_run)
        logger.debug("LLM analysis submitted to thread pool")

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        format: str | None = "json",
    ) -> str:
        """
        Generate a response from the LLM.

        This is a lower-level method for direct LLM access (e.g., chat endpoints).

        Args:
            prompt: The user prompt
            system: Optional system prompt
            format: Response format ("json" or None for free text)

        Returns:
            The raw response text from the LLM
        """
        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
            }
            if system:
                payload["system"] = system
            if format:
                payload["format"] = format

            response = self._client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")

        except httpx.HTTPError as e:
            logger.error(f"Ollama HTTP error in generate: {e}")
            raise
        except Exception as e:
            logger.error(f"Ollama error in generate: {e}")
            raise

    # ── Chat Intent Interpretation ──────────────────────────────────

    def interpret_message(self, message: str, sqlite: "SqliteClient", ws: "WebSocketServer") -> OllamaIntent:
        """
        Interpret a chat message and return a structured intent.
        Ported from apps/api/src/services/ollama.ts interpretMessage().
        """
        system_prompt = build_system_prompt(sqlite, ws)

        try:
            response = self._client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": message,
                    "system": system_prompt,
                    "stream": False,
                    "format": "json",
                },
            )
            response.raise_for_status()
            data = response.json()
            raw = data.get("response", "")

            return _parse_intent(raw)

        except httpx.HTTPError as e:
            logger.error(f"Ollama HTTP error in interpret_message: {e}")
            raise
        except Exception as e:
            logger.error(f"Ollama error in interpret_message: {e}")
            raise

    async def interpret_message_stream(
        self, message: str, sqlite: "SqliteClient", ws: "WebSocketServer"
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Stream interpretation of a message — yields partial tokens then final intent.
        Ported from apps/api/src/services/ollama.ts interpretMessageStream().
        """
        system_prompt = build_system_prompt(sqlite, ws)

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": message,
                    "system": system_prompt,
                    "stream": True,
                    "format": "json",
                },
            ) as response:
                response.raise_for_status()
                full_response = ""

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        token = data.get("response", "")
                        if token:
                            full_response += token
                            yield {"type": "token", "token": token}
                    except json.JSONDecodeError:
                        pass

        # Parse the complete response
        intent = _parse_intent(full_response)
        yield {"type": "done", "intent": intent}

    def close(self):
        """Close the HTTP client."""
        self._client.close()


# ── System Prompt Builder ────────────────────────────────────────────

def build_system_prompt(sqlite: "SqliteClient", ws: "WebSocketServer") -> str:
    """
    Build dynamic system prompt with device context.
    Ported from apps/api/src/services/systemPrompt.ts.
    """
    devices = sqlite.get_all_devices()
    latest_by_device = ws.get_all_latest_by_device()

    device_sections = []
    for d in devices:
        sensors = "\n".join(
            f"  - {s.id}: {s.type}" + (f" ({s.name})" if s.name else "")
            for s in d.capabilities.sensors
        ) or "  (none)"

        actuators = "\n".join(
            f"  - {a.id}: {a.type}" + (f" ({a.name})" if a.name else "")
            for a in d.capabilities.actuators
        ) or "  (none)"

        status = "online" if d.online else "offline"
        reading = latest_by_device.get(d.id)
        if reading:
            reading_str = f"Current readings: temperature={reading.get('temp', 0):.1f}°C, humidity={reading.get('humidity', 0):.1f}%"
        else:
            reading_str = "No sensor data available yet."

        device_sections.append(
            f"Device: {d.id} ({d.name or d.id}) at {d.location} [{status}]\n"
            f"Sensors:\n{sensors}\nActuators:\n{actuators}\n{reading_str}"
        )

    device_list = "\n\n".join(device_sections) if device_sections else "No devices registered yet."

    return f"""You are a smart home assistant for an ESP32-based IoT system. Interpret user requests and respond with JSON only.

{device_list}

IMPORTANT: You must respond with valid JSON only. No additional text.
IMPORTANT: Always include "deviceId" to specify which device to target. Use the device names and locations listed above to determine the correct device. If the user does not specify a device, infer it from context or ask for clarification.

For actuator commands (turn on/off relays, etc.), respond:
{{"intent": "command", "deviceId": "<device_id>", "target": "<actuator_id>", "action": "set", "value": <true|false>, "reply": "<friendly response>"}}

For momentary actuators (type: "momentary"), use action "pulse":
{{"intent": "command", "deviceId": "<device_id>", "target": "<actuator_id>", "action": "pulse", "value": true, "reply": "<friendly response>"}}

For sensor queries (what's the temperature, etc.), respond:
{{"intent": "query", "deviceId": "<device_id>", "sensor": "<sensor_id>", "reply": "<friendly response with the actual value>"}}

For historical queries (what happened, show me events, recent commands, etc.), respond:
{{"intent": "history", "deviceId": "<device_id>", "timeframe": "<1h|6h|12h|24h|7d|30d>", "category": "<commands|events|all>", "reply": "<friendly response acknowledging the request>", "summary": "<1-3 sentence spoken summary>"}}

For sensor data analysis (trends, anomalies, spikes, fluctuations, patterns), respond:
{{"intent": "analyze", "deviceId": "<device_id>", "timeframe": "<1h|6h|12h|24h|7d|30d>", "metric": "<temperature|humidity|all>", "reply": "<friendly response acknowledging the analysis request>", "summary": "<1-3 sentence spoken summary>"}}

For unclear or unrelated requests, respond:
{{"intent": "none", "reply": "<helpful clarification>"}}

Examples:
User: "turn on the grow room light"
{{"intent": "command", "deviceId": "esp32-1", "target": "relay1", "action": "set", "value": true, "reply": "Turning on the grow room light."}}

User: "what's the temperature?"
{{"intent": "query", "deviceId": "esp32-1", "sensor": "temp1", "reply": "The current temperature is 22.5°C."}}

User: "what happened in the last hour?"
{{"intent": "history", "deviceId": "esp32-1", "timeframe": "1h", "category": "all", "reply": "Here's what happened in the last hour.", "summary": "A quiet hour with no commands or notable events."}}

User: "any temperature spikes?"
{{"intent": "analyze", "deviceId": "esp32-1", "timeframe": "24h", "metric": "temperature", "reply": "Let me analyze the temperature data for anomalies.", "summary": "Temperature stayed stable around 22°C with no significant spikes detected."}}"""


def _parse_intent(raw: str) -> OllamaIntent:
    """Parse raw LLM response into a structured intent."""
    try:
        logger.info(f"Ollama raw response: {raw}")
        parsed = json.loads(raw)
        logger.info(f"Parsed intent: {parsed.get('intent')}")

        if not parsed.get("intent") or not parsed.get("reply"):
            raise ValueError("Invalid response structure")

        if parsed["intent"] == "command":
            if not parsed.get("target") or not parsed.get("action"):
                raise ValueError("Command missing target or action")

        if parsed["intent"] == "analyze" and not parsed.get("timeframe"):
            parsed["timeframe"] = "24h"

        if parsed["intent"] == "history" and not parsed.get("timeframe"):
            parsed["timeframe"] = "24h"

        return parsed

    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse Ollama response: {raw}, error: {e}")
        fallback = (
            f'I received: "{raw[:200]}..." but couldn\'t process it properly. '
            'Try asking more specifically, like "analyze temperature for the last 24 hours".'
            if 0 < len(raw) < 500
            else "I had trouble understanding that. Try asking something like 'analyze temperature spikes in the last 24 hours'."
        )
        return {"intent": "none", "reply": fallback}
