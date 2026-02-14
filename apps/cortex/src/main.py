#!/usr/bin/env python3
"""
Cortex — unified backend for ESP32 IoT system.

Bootstraps all services:
1. FastAPI (REST API, WebSocket, voice endpoints) via uvicorn
2. MQTT client (telemetry ingestion, command publishing, device registry)
3. Decision engine (rules-based automation + LLM escalation)
4. Background jobs (aggregation, command expiration)

The FastAPI app (voice_api.py) handles storage initialization, route mounting,
and background jobs. This module adds the MQTT client and decision engine on top.
"""

import asyncio
import logging
import signal
import sys
import threading
import time
from pathlib import Path

import uvicorn

from .config import RULES_PATH, HTTP_PORT, SQLITE_PATH, SQLITE_JOURNAL_MODE, REDIS_URL
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
    """Main Cortex orchestrator — coordinates MQTT, decision engine, and API server."""

    def __init__(self):
        self._shared = get_shared_services()
        self.mqtt: MqttService | None = None
        self.engine: DecisionEngine | None = None
        self._pending_llm_analysis = False
        self._http_server: uvicorn.Server | None = None
        self._http_thread: threading.Thread | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None

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
        """Start all Cortex services."""
        logger.info("Starting Cortex...")

        # Load decision engine
        rules_path = Path(RULES_PATH)
        if rules_path.exists():
            self.engine = DecisionEngine.from_yaml(rules_path)
            logger.info(f"Loaded {len(self.engine.rules)} rules from {rules_path}")
        else:
            logger.warning(f"Rules file not found: {rules_path}, using empty ruleset")
            self.engine = DecisionEngine()

        # Initialize Ollama
        ollama = self._shared.init_ollama()
        if ollama.is_available():
            logger.info("Ollama LLM is available")
        else:
            logger.warning("Ollama LLM is not available — running rules-only mode")

        # Start the FastAPI server (which initializes storage, WebSocket, voice, routes, jobs)
        self._start_api_server()

        # Wait for the API server to initialize storage
        logger.info("Waiting for API server to initialize...")
        self._wait_for_api()

        # Get service references from the running FastAPI app
        from .voice_api import app, sqlite, redis_client, ws_server
        self._event_loop = asyncio.new_event_loop()

        # Start event loop in background thread for async operations from MQTT callbacks
        def run_loop():
            asyncio.set_event_loop(self._event_loop)
            self._event_loop.run_forever()

        loop_thread = threading.Thread(target=run_loop, daemon=True)
        loop_thread.start()

        # Initialize MQTT with storage + WebSocket + decision engine
        self.mqtt = MqttService(
            on_telemetry=self._handle_telemetry,
            on_ack=self._handle_ack,
            on_device_birth=self._handle_device_birth,
            on_device_offline=self._handle_device_offline,
            sqlite=sqlite,
            redis_client=redis_client,
            ws_server=ws_server,
            event_loop=self._event_loop,
        )

        # Inject MQTT into route handlers that need it
        # (chat, voice, relays, commands routes need mqtt for publishing)
        if hasattr(app, 'state'):
            app.state.mqtt = self.mqtt

        self.mqtt.connect()
        self.running = True

        logger.info("Cortex running. Press Ctrl+C to stop.")
        logger.info(f"  API:       http://localhost:{HTTP_PORT}")
        logger.info(f"  WebSocket: ws://localhost:{HTTP_PORT}/ws")

        try:
            self.mqtt.loop_forever()
        except KeyboardInterrupt:
            pass

        self.stop()

    def _start_api_server(self):
        """Start the FastAPI server in a background thread."""
        from .voice_api import app

        config = uvicorn.Config(
            app, host="0.0.0.0", port=HTTP_PORT, log_level="info",
        )
        self._http_server = uvicorn.Server(config)

        def run_server():
            self._http_server.run()

        self._http_thread = threading.Thread(target=run_server, daemon=True)
        self._http_thread.start()
        logger.info(f"API server starting on port {HTTP_PORT}")

    def _wait_for_api(self):
        """Wait for the API server to be ready."""
        import httpx
        for _ in range(30):
            try:
                r = httpx.get(f"http://localhost:{HTTP_PORT}/health", timeout=2)
                if r.status_code == 200:
                    logger.info("API server ready")
                    return
            except Exception:
                pass
            time.sleep(1)
        logger.warning("API server did not become ready in time, continuing anyway")

    def stop(self):
        """Stop all services."""
        logger.info("Stopping Cortex...")
        self.running = False

        if self._http_server:
            self._http_server.should_exit = True

        if self.mqtt:
            self.mqtt.disconnect()
            self.mqtt.loop_stop()

        if self._event_loop:
            self._event_loop.call_soon_threadsafe(self._event_loop.stop)

        self._shared.close()
        logger.info("Cortex stopped")

    # ── Decision Engine Handlers ─────────────────────────────────────

    def _handle_telemetry(self, telemetry: TelemetryMessage):
        """Decision engine: evaluate rules against telemetry."""
        logger.debug(f"Evaluating rules for {telemetry.device_id}")

        commands = self.engine.evaluate(telemetry)
        for command in commands:
            self._execute_command(command)

        if not commands and self.engine.should_escalate_to_llm(telemetry):
            if self.ollama and self.ollama.is_available() and not self._pending_llm_analysis:
                logger.info("Escalating to LLM for analysis")
                self._pending_llm_analysis = True
                self.ollama.analyze_async(
                    telemetry,
                    self.engine.get_context_summary(),
                    self.recent_commands,
                    callback=self._handle_llm_result,
                )

    def _handle_llm_result(self, command: Command | None):
        self._pending_llm_analysis = False
        if command:
            logger.info(f"LLM analysis: executing {command.target}")
            self._execute_command(command)

    def _execute_command(self, command: Command):
        if not self.mqtt:
            logger.error("MQTT not connected")
            return

        correlation_id = self.mqtt.publish_command(command)
        logger.info(f"Executed command {correlation_id}: {command.target} = {command.value}")

        self.recent_commands.append({
            "correlation_id": correlation_id,
            "target": command.target,
            "action": command.action,
            "value": command.value,
            "reason": command.reason,
            "ts": time.time(),
        })

        cutoff = time.time() - 300
        self._shared.state.recent_commands[:] = [
            c for c in self.recent_commands if c["ts"] > cutoff
        ]

    def _handle_ack(self, ack: CommandAck):
        logger.info(f"Command {ack.correlation_id} {ack.status}: {ack.target} = {ack.actual_value}")
        if ack.status != "executed":
            logger.warning(f"Command failed: {ack.error}")

    def _handle_device_birth(self, device_id: str, location: str, capabilities: dict):
        self.devices[device_id] = {
            "location": location,
            "capabilities": capabilities,
            "online": True,
            "last_seen": time.time(),
        }
        logger.info(f"Device registered: {device_id} at {location}")

    def _handle_device_offline(self, device_id: str):
        if device_id in self.devices:
            self.devices[device_id]["online"] = False
        logger.warning(f"Device offline: {device_id}")


def main():
    """Entry point."""
    orchestrator = Orchestrator()

    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        orchestrator.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    orchestrator.start()


if __name__ == "__main__":
    main()
