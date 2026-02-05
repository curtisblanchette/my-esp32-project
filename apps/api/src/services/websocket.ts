import { WebSocketServer, WebSocket } from "ws";
import type { Server } from "http";
import { getLatest, getAllLatestByDevice } from "../state/latestReading.js";
import { getAllDevices, getDeviceActuators, queryEvents, queryCommands, type Command, type Event } from "../lib/sqlite.js";

const clients = new Set<WebSocket>();

export function createWebSocketServer(server: Server): WebSocketServer {
  const wss = new WebSocketServer({ server, path: "/ws" });

  wss.on("connection", (ws: WebSocket) => {
    console.log("WebSocket client connected");
    clients.add(ws);

    // Send initial per-device latest readings on connection
    const latestByDevice = getAllLatestByDevice();
    for (const [deviceId, reading] of Object.entries(latestByDevice)) {
      ws.send(JSON.stringify({
        type: "latest",
        data: reading,
      }));
    }
    // Also send global latest for backward compatibility
    const latest = getLatest();
    if (latest && !latest.deviceId) {
      ws.send(JSON.stringify({
        type: "latest",
        data: latest,
      }));
    }

    // Send devices
    try {
      const devices = getAllDevices();
      ws.send(JSON.stringify({
        type: "devices",
        data: devices,
      }));
    } catch (error) {
      console.error("Error fetching devices for WebSocket:", error);
    }

    // Send relay status (derived from device actuators, merged with config)
    try {
      const relays = buildRelayList();
      ws.send(JSON.stringify({
        type: "relays",
        data: relays,
      }));
    } catch (error) {
      console.error("Error fetching relays for WebSocket:", error);
    }

    // Send recent events
    try {
      const sinceMs = Date.now() - 60 * 60 * 1000;
      const events = queryEvents({ sinceMs, limit: 20 });
      const mappedEvents = events.map((e) => ({
        id: String(e.id),
        ts: e.ts,
        deviceId: e.deviceId,
        eventType: e.eventType,
        data: e.payload,
        source: e.source,
      }));
      ws.send(JSON.stringify({ type: "events", data: mappedEvents }));
    } catch (error) {
      console.error("Error fetching events for WebSocket:", error);
    }

    // Send recent commands
    try {
      const sinceMs = Date.now() - 60 * 60 * 1000;
      const commands = queryCommands({ sinceMs, limit: 20 });
      ws.send(JSON.stringify({ type: "commands", data: commands }));
    } catch (error) {
      console.error("Error fetching commands for WebSocket:", error);
    }

    ws.on("close", () => {
      console.log("WebSocket client disconnected");
      clients.delete(ws);
    });

    ws.on("error", (error) => {
      console.error("WebSocket error:", error);
      clients.delete(ws);
    });
  });

  return wss;
}

export function broadcastLatestReading(reading: any): void {
  const message = JSON.stringify({
    type: "latest",
    data: reading,
  });

  clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(message);
    }
  });
}

export function broadcastRelayUpdate(relays: any[]): void {
  const message = JSON.stringify({
    type: "relays",
    data: relays,
  });

  clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(message);
    }
  });
}

export function broadcastDevices(): void {
  const devices = getAllDevices();
  const devicesMessage = JSON.stringify({
    type: "devices",
    data: devices,
  });

  // Also broadcast updated relay list (derived from devices)
  const relays = buildRelayList();
  const relaysMessage = JSON.stringify({
    type: "relays",
    data: relays,
  });

  clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(devicesMessage);
      client.send(relaysMessage);
    }
  });
}

function buildRelayList(): Array<{
  id: string;
  name: string;
  state: boolean;
  updatedAt: number;
  deviceId: string;
  location: string;
  deviceOnline: boolean;
}> {
  const actuators = getDeviceActuators();

  return actuators.map((actuator) => ({
    id: actuator.id,
    name: actuator.customName ?? actuator.name ?? actuator.id,
    state: actuator.state ?? false,
    updatedAt: Date.now(),
    deviceId: actuator.deviceId,
    location: actuator.location,
    deviceOnline: actuator.deviceOnline,
  }));
}

export function broadcastCommandUpdate(commands: Command[]): void {
  if (commands.length === 0) return;

  const message = JSON.stringify({
    type: "commands",
    data: commands,
  });

  clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(message);
    }
  });
}

export function broadcastCommand(command: Command): void {
  const message = JSON.stringify({ type: "command", data: command });
  clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(message);
    }
  });
}

export function broadcastEvent(event: Event): void {
  const mapped = {
    id: String(event.id),
    ts: event.ts,
    deviceId: event.deviceId,
    eventType: event.eventType,
    data: event.payload,
    source: event.source,
  };
  const message = JSON.stringify({ type: "event", data: mapped });
  clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(message);
    }
  });
}
