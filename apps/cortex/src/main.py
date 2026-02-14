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
        # Phase 1: trend context and baselines
        self._data_reader = None
        self._memory = None
        self._context_cache: dict | None = None
        self._context_cache_ts: float = 0.0
        self._context_cache_ttl: float = 30.0

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

        # Phase 1: Initialize DataReader and CortexMemory
        from .services.data_reader import DataReader
        from .services.cortex_memory import CortexMemory
        self._data_reader = DataReader(redis_client, sqlite)
        self._memory = CortexMemory(sqlite)
        logger.info("Phase 1: DataReader and CortexMemory initialized")

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
        """Decision engine: evaluate rules with trend context."""
        logger.debug(f"Evaluating rules for {telemetry.device_id}")

        # Build or reuse cached context (Phase 1)
        context = self._build_context(telemetry.device_id)

        # Evaluate rules with context
        commands = self.engine.evaluate(telemetry, context)
        for command in commands:
            self._execute_command(command)

        # LLM escalation with enriched context
        if not commands and self.engine.should_escalate_to_llm(telemetry):
            if self.ollama and self.ollama.is_available() and not self._pending_llm_analysis:
                logger.info("Escalating to LLM for analysis")
                self._pending_llm_analysis = True
                enriched = {**self.engine.get_context_summary()}
                if context:
                    enriched["_trends"] = context.get("trends", {})
                    enriched["_baselines"] = context.get("baselines", {})
                self.ollama.analyze_async(
                    telemetry,
                    enriched,
                    self.recent_commands,
                    callback=self._handle_llm_result,
                )

        # Update baselines (cheap, incremental)
        self._update_baselines(telemetry)

    def _build_context(self, device_id: str) -> dict | None:
        """Build decision context from recent history. Cached for 30 seconds."""
        now = time.time()
        if self._context_cache and (now - self._context_cache_ts) < self._context_cache_ttl:
            return self._context_cache

        if not self._data_reader:
            return None

        try:
            readings = self._data_reader.get_recent_readings(window_minutes=30, device_id=device_id)
            if len(readings) < 3:
                return None

            from .services.analysis import build_trend_context

            temp_values, temp_ts = self._data_reader.extract_metric(readings, "temperature")
            hum_values, hum_ts = self._data_reader.extract_metric(readings, "humidity")

            trends = {}
            temp_trend = build_trend_context("temperature", temp_values, temp_ts)
            if temp_trend:
                trends["temp1"] = {
                    "trend": temp_trend.trend,
                    "rate": temp_trend.rate_of_change,
                    "mean": temp_trend.mean_30m,
                    "current": temp_trend.current_value,
                }
            hum_trend = build_trend_context("humidity", hum_values, hum_ts)
            if hum_trend:
                trends["hum1"] = {
                    "trend": hum_trend.trend,
                    "rate": hum_trend.rate_of_change,
                    "mean": hum_trend.mean_30m,
                    "current": hum_trend.current_value,
                }

            # Get baselines for current hour
            baselines = {}
            if self._memory:
                from datetime import datetime
                current_hour = datetime.now().hour
                for sensor, metric in [("temp1", "temperature"), ("hum1", "humidity")]:
                    baseline = self._memory.get_baseline(device_id, metric, current_hour)
                    if baseline and baseline.sample_count >= 10:
                        current = trends.get(sensor, {}).get("current")
                        deviation = None
                        if current is not None and baseline.std_dev > 0:
                            deviation = (current - baseline.avg_value) / baseline.std_dev
                        baselines[sensor] = {
                            "avg": baseline.avg_value,
                            "std_dev": baseline.std_dev,
                            "samples": baseline.sample_count,
                            "deviation": deviation,
                        }

            self._context_cache = {
                "trends": trends,
                "baselines": baselines,
                "built_at": now,
            }
            self._context_cache_ts = now

            if trends:
                parts = [f"{k}={v['trend']}({v['rate']:+.3f}/min)" for k, v in trends.items()]
                logger.debug(f"Context built: {', '.join(parts)}")

            return self._context_cache

        except Exception as e:
            logger.error(f"Failed to build context: {e}")
            return None

    def _update_baselines(self, telemetry: TelemetryMessage) -> None:
        """Update hourly baselines from current telemetry (incremental)."""
        if not self._memory:
            return
        try:
            from datetime import datetime
            current_hour = datetime.now().hour

            temp_reading = telemetry.get_reading("temp1")
            if temp_reading and isinstance(temp_reading.value, (int, float)):
                self._memory.update_baseline(
                    telemetry.device_id, "temperature", current_hour, float(temp_reading.value),
                )

            hum_reading = telemetry.get_reading("hum1")
            if hum_reading and isinstance(hum_reading.value, (int, float)):
                self._memory.update_baseline(
                    telemetry.device_id, "humidity", current_hour, float(hum_reading.value),
                )
        except Exception as e:
            logger.error(f"Failed to update baselines: {e}")

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
