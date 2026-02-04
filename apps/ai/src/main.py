#!/usr/bin/env python3
"""
AI Orchestrator for ESP32 IoT System

Monitors sensor telemetry and makes decisions using:
1. Rule-based engine for threshold-based actions
2. Ollama LLM for complex pattern analysis
"""

import logging
import signal
import sys
import threading
import time
from pathlib import Path

import uvicorn

from .config import RULES_PATH, HTTP_PORT
from .models.telemetry import TelemetryMessage
from .models.command import Command, CommandAck
from .services.mqtt_client import MqttService
from .services.decision_engine import DecisionEngine
from .services.shared import get_shared_services

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class Orchestrator:
    """Main AI orchestrator that coordinates all components."""

    def __init__(self):
        self._shared = get_shared_services()
        self.mqtt: MqttService | None = None
        self.engine: DecisionEngine | None = None
        self._pending_llm_analysis = False  # Prevent overlapping LLM requests
        self._http_server: uvicorn.Server | None = None
        self._http_thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._shared.state.running

    @running.setter
    def running(self, value: bool) -> None:
        self._shared.state.running = value

    @property
    def devices(self) -> dict[str, dict]:
        return self._shared.state.devices

    @property
    def recent_commands(self) -> list[dict]:
        return self._shared.state.recent_commands

    @property
    def ollama(self):
        return self._shared.ollama

    def start(self):
        """Start the orchestrator."""
        logger.info("Starting AI Orchestrator...")

        # Load decision engine with rules
        rules_path = Path(RULES_PATH)
        if rules_path.exists():
            self.engine = DecisionEngine.from_yaml(rules_path)
            logger.info(f"Loaded {len(self.engine.rules)} rules from {rules_path}")
        else:
            logger.warning(f"Rules file not found: {rules_path}, using empty ruleset")
            self.engine = DecisionEngine()

        # Initialize shared Ollama client
        ollama = self._shared.init_ollama()
        if ollama.is_available():
            logger.info("Ollama LLM is available")
        else:
            logger.warning("Ollama LLM is not available - running rules-only mode")

        # Start HTTP API server in background thread
        self._start_http_server()

        # Initialize MQTT client
        self.mqtt = MqttService(
            on_telemetry=self._handle_telemetry,
            on_ack=self._handle_ack,
            on_device_birth=self._handle_device_birth,
            on_device_offline=self._handle_device_offline,
        )

        # Connect to MQTT
        self.mqtt.connect()
        self.running = True

        # Run the main loop
        logger.info("AI Orchestrator running. Press Ctrl+C to stop.")
        try:
            self.mqtt.loop_forever()
        except KeyboardInterrupt:
            pass

        self.stop()

    def _start_http_server(self):
        """Start the HTTP API server in a background thread."""
        from .api import app

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=HTTP_PORT,
            log_level="info",
        )
        self._http_server = uvicorn.Server(config)

        def run_server():
            self._http_server.run()

        self._http_thread = threading.Thread(target=run_server, daemon=True)
        self._http_thread.start()
        logger.info(f"HTTP API server started on port {HTTP_PORT}")

    def stop(self):
        """Stop the orchestrator."""
        logger.info("Stopping AI Orchestrator...")
        self.running = False

        if self._http_server:
            self._http_server.should_exit = True

        if self.mqtt:
            self.mqtt.disconnect()
            self.mqtt.loop_stop()

        self._shared.close()

        logger.info("AI Orchestrator stopped")

    def _handle_telemetry(self, telemetry: TelemetryMessage):
        """Handle incoming telemetry data."""
        logger.debug(f"Received telemetry from {telemetry.device_id}")

        # Evaluate rules (fast, synchronous)
        commands = self.engine.evaluate(telemetry)

        # Execute rule-based commands immediately
        for command in commands:
            self._execute_command(command)

        # Check if we should escalate to LLM (non-blocking)
        if not commands and self.engine.should_escalate_to_llm(telemetry):
            if self.ollama and self.ollama.is_available() and not self._pending_llm_analysis:
                logger.info("Escalating to LLM for analysis (async)")
                self._pending_llm_analysis = True
                self.ollama.analyze_async(
                    telemetry,
                    self.engine.get_context_summary(),
                    self.recent_commands,
                    callback=self._handle_llm_result,
                )

    def _handle_llm_result(self, command: Command | None):
        """Callback for async LLM analysis results."""
        self._pending_llm_analysis = False
        if command:
            logger.info(f"LLM analysis complete, executing command: {command.target}")
            self._execute_command(command)
        else:
            logger.debug("LLM analysis complete, no action needed")

    def _execute_command(self, command: Command):
        """Execute a command by publishing to MQTT."""
        if not self.mqtt:
            logger.error("MQTT not connected, cannot execute command")
            return

        correlation_id = self.mqtt.publish_command(command)
        logger.info(f"Executed command {correlation_id}: {command.target} = {command.value}")

        # Track recent commands
        self.recent_commands.append({
            "correlation_id": correlation_id,
            "target": command.target,
            "action": command.action,
            "value": command.value,
            "reason": command.reason,
            "ts": time.time(),
        })

        # Keep only last 5 minutes of commands
        cutoff = time.time() - 300
        self._shared.state.recent_commands[:] = [
            c for c in self.recent_commands if c["ts"] > cutoff
        ]

    def _handle_ack(self, ack: CommandAck):
        """Handle command acknowledgment."""
        logger.info(f"Command {ack.correlation_id} {ack.status}: {ack.target} = {ack.actual_value}")

        if ack.status != "executed":
            logger.warning(f"Command failed: {ack.error}")

    def _handle_device_birth(self, device_id: str, location: str, capabilities: dict):
        """Handle device coming online."""
        self.devices[device_id] = {
            "location": location,
            "capabilities": capabilities,
            "online": True,
            "last_seen": time.time(),
        }
        logger.info(f"Device registered: {device_id} at {location}")
        logger.debug(f"Capabilities: {capabilities}")

    def _handle_device_offline(self, device_id: str):
        """Handle device going offline."""
        if device_id in self.devices:
            self.devices[device_id]["online"] = False
        logger.warning(f"Device offline: {device_id}")


def main():
    """Entry point."""
    orchestrator = Orchestrator()

    # Handle signals
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        orchestrator.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    orchestrator.start()


if __name__ == "__main__":
    main()
