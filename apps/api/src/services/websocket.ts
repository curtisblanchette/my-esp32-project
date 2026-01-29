import { WebSocketServer, WebSocket } from "ws";
import type { Server } from "http";
import { getLatest } from "../state/latestReading.js";
import { getAllRelayConfigs } from "../lib/sqlite.js";

const clients = new Set<WebSocket>();

export function createWebSocketServer(server: Server): WebSocketServer {
  const wss = new WebSocketServer({ server, path: "/ws" });

  wss.on("connection", (ws: WebSocket) => {
    console.log("WebSocket client connected");
    clients.add(ws);

    // Send initial data on connection
    const latest = getLatest();
    if (latest) {
      ws.send(JSON.stringify({
        type: "latest",
        data: latest,
      }));
    }

    // Send relay status
    try {
      const configs = getAllRelayConfigs();
      const relays = configs.map((config) => ({
        id: config.id,
        name: config.name,
        state: Boolean(config.enabled),
        updatedAt: config.updatedAt,
      }));
      ws.send(JSON.stringify({
        type: "relays",
        data: relays,
      }));
    } catch (error) {
      console.error("Error fetching relays for WebSocket:", error);
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
