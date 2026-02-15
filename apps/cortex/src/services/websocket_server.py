"""
WebSocket server for real-time dashboard updates.

Ported from apps/api/src/services/websocket.ts — same message types and
on-connect behavior to maintain frontend compatibility.
"""

import json
import logging
import time
from typing import Any, TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

if TYPE_CHECKING:
    from .sqlite_client import SqliteClient, Command, Event

logger = logging.getLogger(__name__)


class WebSocketServer:
    """Manages WebSocket connections and broadcasts."""

    def __init__(self):
        self._clients: set[WebSocket] = set()
        # In-memory latest readings per device (populated by MQTT handler)
        self._latest_by_device: dict[str, dict[str, Any]] = {}

    async def handle_connection(self, ws: WebSocket, sqlite: "SqliteClient") -> None:
        """Handle a new WebSocket connection."""
        await ws.accept()
        self._clients.add(ws)
        logger.info(f"WebSocket client connected ({len(self._clients)} total)")

        try:
            # Send initial state
            await self._send_initial_state(ws, sqlite)

            # Keep connection alive, listen for messages (ping/pong handled by Starlette)
            while True:
                # We don't expect messages from clients, but need to consume to detect disconnect
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug(f"WebSocket error: {e}")
        finally:
            self._clients.discard(ws)
            logger.info(f"WebSocket client disconnected ({len(self._clients)} total)")

    async def _send_initial_state(self, ws: WebSocket, sqlite: "SqliteClient") -> None:
        """Send current state to a newly connected client."""
        # Send per-device latest readings
        for device_id, reading in self._latest_by_device.items():
            await self._send(ws, {"type": "latest", "data": reading})

        # Send devices
        try:
            devices = sqlite.get_all_devices()
            await self._send(ws, {
                "type": "devices",
                "data": [d.to_dict() for d in devices],
            })
        except Exception as e:
            logger.error(f"Error fetching devices for WebSocket: {e}")

        # Send relay list
        try:
            relays = self._build_relay_list(sqlite)
            await self._send(ws, {"type": "relays", "data": relays})
        except Exception as e:
            logger.error(f"Error fetching relays for WebSocket: {e}")

        # Send recent events
        try:
            since_ms = int(time.time() * 1000) - 60 * 60 * 1000
            events = sqlite.query_events(since_ms=since_ms, limit=20)
            mapped = [sqlite.event_to_dict(e) for e in events]
            await self._send(ws, {"type": "events", "data": mapped})
        except Exception as e:
            logger.error(f"Error fetching events for WebSocket: {e}")

        # Send recent commands
        try:
            since_ms = int(time.time() * 1000) - 60 * 60 * 1000
            commands = sqlite.query_commands(since_ms=since_ms, limit=20)
            await self._send(ws, {
                "type": "commands",
                "data": [sqlite.command_to_dict(c) for c in commands],
            })
        except Exception as e:
            logger.error(f"Error fetching commands for WebSocket: {e}")

        # Send rule suggestions
        try:
            suggestions = sqlite.get_suggestions(limit=20)
            await self._send(ws, {"type": "suggestions", "data": suggestions})
        except Exception as e:
            logger.error(f"Error fetching suggestions for WebSocket: {e}")

    def set_latest(self, reading: dict[str, Any]) -> None:
        """Update in-memory latest reading for a device."""
        device_id = reading.get("deviceId")
        if device_id:
            self._latest_by_device[device_id] = reading

    def get_latest_by_device(self, device_id: str) -> dict[str, Any] | None:
        """Get the latest reading for a specific device."""
        return self._latest_by_device.get(device_id)

    def get_all_latest_by_device(self) -> dict[str, dict[str, Any]]:
        """Get all latest readings keyed by device ID."""
        return dict(self._latest_by_device)

    # ── Broadcast Methods ────────────────────────────────────────────

    async def broadcast_latest(self, reading: dict[str, Any]) -> None:
        """Broadcast a sensor reading to all connected clients."""
        self.set_latest(reading)
        await self._broadcast({"type": "latest", "data": reading})

    async def broadcast_devices(self, sqlite: "SqliteClient") -> None:
        """Broadcast updated device list and relay list."""
        devices = sqlite.get_all_devices()
        await self._broadcast({
            "type": "devices",
            "data": [d.to_dict() for d in devices],
        })
        relays = self._build_relay_list(sqlite)
        await self._broadcast({"type": "relays", "data": relays})

    async def broadcast_relay_update(self, relays: list[dict]) -> None:
        """Broadcast relay state update."""
        await self._broadcast({"type": "relays", "data": relays})

    async def broadcast_command(self, command: dict[str, Any]) -> None:
        """Broadcast a command (new or status update)."""
        await self._broadcast({"type": "command", "data": command})

    async def broadcast_event(self, event: dict[str, Any]) -> None:
        """Broadcast an event."""
        await self._broadcast({"type": "event", "data": event})

    async def broadcast_suggestions(self, suggestions: list[dict[str, Any]]) -> None:
        """Broadcast rule adjustment suggestions."""
        await self._broadcast({"type": "suggestions", "data": suggestions})

    async def broadcast_rules(self, rules: list[dict[str, Any]]) -> None:
        """Broadcast current rule states."""
        await self._broadcast({"type": "rules", "data": rules})

    # ── Internal ─────────────────────────────────────────────────────

    def _build_relay_list(self, sqlite: "SqliteClient") -> list[dict]:
        """Build relay list from device actuators."""
        actuators = sqlite.get_device_actuators()
        return [
            {
                "id": a.id,
                "name": a.custom_name or a.name or a.id,
                "type": a.type or "switch",
                "state": a.state if a.state is not None else False,
                "updatedAt": int(time.time() * 1000),
                "deviceId": a.device_id,
                "location": a.location,
                "deviceOnline": a.device_online,
            }
            for a in actuators
        ]

    async def _broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to all connected clients."""
        if not self._clients:
            return
        data = json.dumps(message)
        disconnected: list[WebSocket] = []
        for client in self._clients:
            try:
                if client.client_state == WebSocketState.CONNECTED:
                    await client.send_text(data)
            except Exception:
                disconnected.append(client)
        for client in disconnected:
            self._clients.discard(client)

    async def _send(self, ws: WebSocket, message: dict[str, Any]) -> None:
        """Send a message to a single client."""
        try:
            await ws.send_text(json.dumps(message))
        except Exception as e:
            logger.debug(f"Failed to send to WebSocket: {e}")
