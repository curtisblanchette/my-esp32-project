#!/usr/bin/env python3
"""
Cortex — unified backend for ESP32 IoT system.

Bootstraps all services:
1. FastAPI (REST API, WebSocket, voice endpoints) via uvicorn
2. MQTT client (telemetry ingestion, command publishing, device registry)
3. MPC controller (Model Predictive Control)
4. Background jobs (aggregation, command expiration)

The FastAPI app (voice_api.py) handles storage initialization, route mounting,
and background jobs. This module adds the MQTT client and MPC controller on top.
"""

import asyncio
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

import uvicorn

from .config import RULES_PATH, HTTP_PORT, SQLITE_PATH, SQLITE_JOURNAL_MODE, REDIS_URL, ROOM_CONFIG_PATH
from .models.telemetry import TelemetryMessage
from .models.command import Command, CommandAck
from .services.mqtt_client import MqttService
from .services.shared import get_shared_services

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class Orchestrator:
    """Main Cortex orchestrator — coordinates MQTT, MPC controller, and API server."""

    def __init__(self):
        self._shared = get_shared_services()
        self.mqtt: MqttService | None = None
        self._http_server: uvicorn.Server | None = None
        self._http_thread: threading.Thread | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        # MPC controller
        self._mpc_controller = None

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

        # Initialize Ollama
        ollama = self._shared.init_ollama()
        if ollama.is_available():
            logger.info("Ollama LLM is available")
        else:
            logger.warning("Ollama LLM is not available")

        # Start the FastAPI server (which initializes storage, WebSocket, voice, routes, jobs)
        self._start_api_server()

        # Wait for the API server to initialize storage
        logger.info("Waiting for API server to initialize...")
        self._wait_for_api()

        # Get service references from the running FastAPI app
        from .voice_api import app, sqlite, redis_client, ws_server
        self._sqlite = sqlite
        self._event_loop = asyncio.new_event_loop()

        # Load LLM config from YAML
        rules_path = Path(RULES_PATH)
        if rules_path.exists():
            import yaml
            with open(rules_path) as f:
                yaml_config = yaml.safe_load(f)
            # LLM config stays in YAML — used by Ollama client
            llm_config = yaml_config.get("llm", {})
            logger.info(f"Loaded LLM config from {rules_path}")

        # Start event loop in background thread for async operations from MQTT callbacks
        def run_loop():
            asyncio.set_event_loop(self._event_loop)
            self._event_loop.run_forever()

        loop_thread = threading.Thread(target=run_loop, daemon=True)
        loop_thread.start()

        # Mount cortex API routes
        from .api.cortex import create_cortex_router
        app.include_router(
            create_cortex_router(sqlite, ws_server),
            prefix="/api/cortex",
        )
        logger.info("/api/cortex routes mounted")

        # Initialize ChatSessionStore for multi-turn chat
        from .services.chat_session import ChatSessionStore
        self._chat_session_store = ChatSessionStore()
        app.state.chat_session_store = self._chat_session_store
        logger.info("ChatSessionStore initialized")

        # MPC Controller (requires ROOM_CONFIG_PATH)
        if ROOM_CONFIG_PATH:
            try:
                from .services.mpc_controller import MPCController
                from simulations.room_config import load_room_config
                room_config = load_room_config(ROOM_CONFIG_PATH)
                # Load goals from first active profile
                mpc_goals = []
                profiles = sqlite.get_all_profiles()
                for p in profiles:
                    if p.get("active"):
                        mpc_goals = sqlite.get_goals_for_profile(
                            p["id"], phase=p.get("phase"),
                        )
                        break
                # MPC needs an MQTT ref — will be set after MQTT init below
                self._mpc_controller = MPCController(
                    room_config=room_config,
                    goals=mpc_goals,
                    mqtt_client=None,  # set after MQTT init
                    location=os.environ.get("DEFAULT_LOCATION", "room1"),
                    device_id=os.environ.get("DEFAULT_DEVICE_ID", "esp32-1"),
                )
                logger.info(f"MPC Controller initialized from {ROOM_CONFIG_PATH} with {len(mpc_goals)} goals")
            except Exception as e:
                logger.error(f"Failed to initialize MPC controller: {e}")
                self._mpc_controller = None

        # Initialize MQTT with storage + WebSocket
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
        if hasattr(app, 'state'):
            app.state.mqtt = self.mqtt

        # Wire MQTT into MPC controller (deferred because MQTT starts after MPC init)
        if self._mpc_controller:
            self._mpc_controller.mqtt = self.mqtt

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

    # ── Telemetry Handlers ──────────────────────────────────────────

    def _handle_telemetry(self, telemetry: TelemetryMessage):
        """MPC controller: process telemetry and generate commands."""
        logger.debug(f"Processing telemetry for {telemetry.device_id}")

        # Build readings dict from telemetry
        current_readings: dict[str, float] = {}
        for reading in telemetry.readings:
            if isinstance(reading.value, (int, float)):
                current_readings[reading.id] = float(reading.value)

        # MPC path
        if self._mpc_controller and current_readings:
            from datetime import datetime
            now = datetime.now()
            current_hour = now.hour + now.minute / 60.0
            commands = self._mpc_controller.on_telemetry(
                telemetry.device_id, current_readings, current_hour,
            )
            for command in commands:
                self.recent_commands.append({
                    "correlation_id": command.correlation_id,
                    "target": command.target,
                    "action": command.action,
                    "value": command.value,
                    "reason": command.reason,
                    "ts": time.time(),
                })

        # Trim recent_commands
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
