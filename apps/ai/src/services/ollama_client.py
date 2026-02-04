import logging
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor

import httpx

from ..config import OLLAMA_URL, OLLAMA_MODEL
from ..models.telemetry import TelemetryMessage
from ..models.command import Command

logger = logging.getLogger(__name__)

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
        """Build the prompt for the LLM."""
        # Format current readings
        readings_str = "\n".join(
            f"  - {r.id}: {r.value}{' ' + r.unit if r.unit else ''}"
            for r in telemetry.readings
        )

        # Format context
        context_str = ""
        if context:
            for device_id, sensors in context.items():
                context_str += f"\nDevice {device_id}:\n"
                for sensor, state in sensors.items():
                    if state.get("last_value") is not None:
                        context_str += f"  - {sensor}: last={state['last_value']}, condition_active={state.get('condition_active', False)}\n"

        # Format recent commands
        commands_str = ""
        if recent_commands:
            commands_str = "\nRecent commands (last 5 minutes):\n"
            for cmd in recent_commands[-5:]:
                commands_str += f"  - {cmd.get('target')}: {cmd.get('action')}={cmd.get('value')} ({cmd.get('reason', 'no reason')})\n"

        prompt = f"""Current sensor readings from {telemetry.device_id} at {telemetry.location}:
{readings_str}

{f"Historical context:{context_str}" if context_str else ""}
{commands_str if commands_str else ""}

Based on these readings, should any action be taken? Consider:
1. Is the temperature comfortable (18-26°C is typical comfort range)?
2. Is humidity at a reasonable level (30-60% is typical)?
3. Are there any concerning trends?

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

    def close(self):
        """Close the HTTP client."""
        self._client.close()
