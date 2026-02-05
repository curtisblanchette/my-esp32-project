import mqtt, { type MqttClient } from "mqtt";
import { randomUUID } from "node:crypto";

import { config } from "../config/index.js";
import { parseTelemetry } from "../lib/telemetry.js";
import { storeReading } from "../lib/redis.js";
import { setLatest } from "../state/latestReading.js";
import { broadcastLatestReading, broadcastDevices, broadcastEvent, broadcastCommand } from "./websocket.js";
import { updateCommandAck, insertEvent, insertCommand, getCommand, upsertDevice, setDeviceOffline, updateActuatorState, type DeviceCapabilities } from "../lib/sqlite.js";

let mqttClient: MqttClient | null = null;

type MessageEnvelope = {
  v: number;
  ts: number;
  deviceId: string;
  location: string;
  type: string;
  correlationId?: string;
  payload: unknown;
};

export function getMqttClient(): MqttClient | null {
  return mqttClient;
}

export function publishCommand(args: {
  deviceId: string;
  location: string;
  target: string;
  action: string;
  value: unknown;
  source: string;
  reason?: string;
  ttl?: number;
}): string | null {
  if (!mqttClient) {
    console.error("MQTT client not connected");
    return null;
  }

  const correlationId = `cmd-${randomUUID().slice(0, 8)}`;
  const topic = `home/${args.location}/${args.deviceId}/command`;

  const envelope: MessageEnvelope & { source: string } = {
    v: 1,
    ts: Date.now(),
    correlationId,
    source: args.source,
    deviceId: args.deviceId,
    location: args.location,
    type: "command",
    payload: {
      target: args.target,
      action: args.action,
      value: args.value,
      reason: args.reason,
      ttl: args.ttl ?? 30000,
    },
  };

  mqttClient.publish(topic, JSON.stringify(envelope), { qos: 1 });
  console.log(`[MQTT] Published command to ${topic}:`, envelope);

  return correlationId;
}

export function initMqttTelemetry(): void {
  mqttClient = mqtt.connect(config.mqttUrl, {
    username: config.mqtt.username,
    password: config.mqtt.password,
    reconnectPeriod: config.mqtt.reconnectPeriod,
  });

  mqttClient.on("connect", () => {
    // Subscribe to new topic patterns
    const topics = [
      "home/+/+/telemetry",    // Sensor readings
      "home/+/+/command",      // Commands (from any source)
      "home/+/+/ack",          // Command acknowledgments
      "home/_registry/+/birth", // Device registration
      "home/_registry/+/will",  // Device offline
    ];

    // Also subscribe to legacy topic for backward compatibility
    const legacyTopic = `${config.topicPrefix}/+/telemetry`;
    topics.push(legacyTopic);

    mqttClient!.subscribe(topics, { qos: 0 }, (err) => {
      if (err) {
        console.error("MQTT subscribe error", err);
        return;
      }
      console.log(`MQTT connected: ${config.mqttUrl}`);
      console.log(`  Subscribed to: ${topics.join(", ")}`);
    });
  });

  mqttClient.on("message", (topic, message) => {
    try {
      const text = message.toString("utf8");
      const json = JSON.parse(text) as unknown;

      // Handle new envelope format
      if (isMessageEnvelope(json)) {
        handleEnvelopeMessage(topic, json);
        return;
      }

      // Handle legacy format (direct telemetry without envelope)
      handleLegacyTelemetry(topic, json);
    } catch (e) {
      console.error("MQTT message parse error", e);
    }
  });

  mqttClient.on("error", (err) => {
    console.error("MQTT error", err);
  });
}

function isMessageEnvelope(json: unknown): json is MessageEnvelope {
  return (
    typeof json === "object" &&
    json !== null &&
    "v" in json &&
    "type" in json &&
    "payload" in json
  );
}

function handleEnvelopeMessage(topic: string, envelope: MessageEnvelope): void {
  const { type, deviceId, location, payload, ts } = envelope;

  switch (type) {
    case "telemetry":
      handleTelemetry(topic, deviceId, payload, ts);
      break;

    case "command":
      handleCommand(envelope);
      break;

    case "ack":
      handleAck(deviceId, payload);
      break;

    case "birth":
      handleBirth(deviceId, location, payload);
      break;

    case "will":
      handleWill(deviceId);
      break;

    default:
      console.log(`[MQTT] Unknown message type: ${type}`);
  }
}

function handleTelemetry(topic: string, deviceId: string, payload: unknown, ts: number): void {
  if (!payload || typeof payload !== "object") return;

  const p = payload as { readings?: Array<{ id: string; value: number }> };
  if (!Array.isArray(p.readings)) return;

  // Extract temp and humidity from readings array
  let temp: number | undefined;
  let humidity: number | undefined;

  for (const reading of p.readings) {
    if (reading.id === "temp1") temp = reading.value;
    if (reading.id === "hum1") humidity = reading.value;
  }

  if (temp === undefined || humidity === undefined) {
    console.log(`[MQTT] Incomplete telemetry from ${deviceId}:`, p.readings);
    return;
  }

  const now = Date.now();
  const latestReading = {
    temp,
    humidity,
    updatedAt: now,
    sourceTopic: topic,
    deviceId,
  };

  setLatest(latestReading);
  broadcastLatestReading(latestReading);

  storeReading({
    ts: now, // Always use server timestamp (device may not have NTP sync)
    temp,
    humidity,
    sourceTopic: topic,
    deviceId,
  }).catch((err) => {
    console.error("Failed to store reading in Redis", err);
  });
}

function handleCommand(envelope: MessageEnvelope & { correlationId?: string; source?: string }): void {
  const { deviceId, ts, correlationId, payload } = envelope;
  const source = envelope.source ?? "unknown";

  if (!correlationId) {
    console.log(`[MQTT] Command missing correlationId from ${source}`);
    return;
  }

  // Check if we already have this command (e.g., we sent it ourselves via API)
  const existing = getCommand(correlationId);
  if (existing) {
    console.log(`[MQTT] Command ${correlationId} already exists, skipping`);
    return;
  }

  const p = payload as {
    target?: string;
    action?: string;
    value?: unknown;
    reason?: string;
  };

  if (!p.target || !p.action) {
    console.log(`[MQTT] Command missing target or action from ${source}`);
    return;
  }

  console.log(`[MQTT] Received command ${correlationId} from ${source}: ${p.target} ${p.action}`);

  const command = insertCommand({
    id: correlationId,
    ts,
    deviceId,
    target: p.target,
    action: p.action,
    value: p.value,
    source,
    reason: p.reason,
  });
  broadcastCommand(command);
}

function handleAck(deviceId: string, payload: unknown): void {
  if (!payload || typeof payload !== "object") return;

  const p = payload as {
    correlationId?: string;
    status?: string;
    target?: string;
    actualValue?: unknown;
    error?: string;
  };

  if (!p.correlationId) {
    console.log(`[MQTT] Ack missing correlationId from ${deviceId}`);
    return;
  }

  console.log(`[MQTT] Ack received for ${p.correlationId}: ${p.status}`, { target: p.target, actualValue: p.actualValue });

  // Map device status to our status
  let status: "acked" | "failed" = "acked";
  if (p.status === "rejected" || p.status === "error" || p.status === "expired") {
    status = "failed";
  }

  // Update command in database
  updateCommandAck(p.correlationId, {
    status,
    ackTs: Date.now(),
    ackPayload: payload,
  });

  // Sync actuator state from ack (if executed successfully)
  // The deviceId comes from the envelope, so we can update the correct device
  if (status === "acked" && p.target && p.actualValue !== undefined) {
    updateActuatorState(deviceId, p.target, Boolean(p.actualValue));
  }

  // Store and broadcast event first, so frontend syncs toggle from event
  // Include the mapped status (acked/failed) so frontend can update command status
  const eventPayload = {
    ...p,
    status, // Use mapped status ("acked" | "failed") instead of raw device status
  };
  const event = insertEvent({
    ts: Date.now(),
    deviceId,
    eventType: "command_ack",
    payload: eventPayload,
    source: "device",
  });
  console.log(`[MQTT] Broadcasting command_ack event:`, { id: event.id, payload: event.payload });
  broadcastEvent(event);

  // Then broadcast updated device/relay state
  if (status === "acked" && p.target && p.actualValue !== undefined) {
    broadcastDevices();
  }
}

function handleBirth(deviceId: string, location: string, payload: unknown): void {
  console.log(`[MQTT] Device birth: ${deviceId} at ${location}`, payload);

  const p = payload as {
    name?: string;
    platform?: string;
    firmware?: string;
    capabilities?: DeviceCapabilities & {
      actuators: Array<{ id: string; type: string; name?: string; state?: boolean }>;
    };
    telemetryIntervalMs?: number;
  } | undefined;

  // Upsert device record with capabilities (actuator states come from device birth)
  upsertDevice({
    id: deviceId,
    location,
    name: p?.name,
    platform: p?.platform,
    firmware: p?.firmware,
    capabilities: p?.capabilities ?? { sensors: [], actuators: [] },
    telemetryIntervalMs: p?.telemetryIntervalMs,
    online: true,
    lastSeen: Date.now(),
  });

  const event = insertEvent({
    ts: Date.now(),
    deviceId,
    eventType: "device_birth",
    payload,
    source: "device",
  });
  broadcastEvent(event);

  // Broadcast updated device list to WebSocket clients
  broadcastDevices();
}

function handleWill(deviceId: string): void {
  console.log(`[MQTT] Device offline: ${deviceId}`);

  // Mark device as offline
  setDeviceOffline(deviceId);

  const event = insertEvent({
    ts: Date.now(),
    deviceId,
    eventType: "device_offline",
    payload: { status: "offline" },
    source: "device",
  });
  broadcastEvent(event);

  // Broadcast updated device list to WebSocket clients
  broadcastDevices();
}

function handleLegacyTelemetry(topic: string, json: unknown): void {
  // Legacy format: { tempC, humidity, ts }
  const reading = parseTelemetry(json);
  if (!reading) return;

  const ts = Date.now();
  const latestReading = {
    temp: reading.temp,
    humidity: reading.humidity,
    updatedAt: ts,
    sourceTopic: topic,
    deviceId: undefined, // Legacy readings don't have deviceId
  };

  setLatest(latestReading);
  broadcastLatestReading(latestReading);

  storeReading({
    ts,
    temp: reading.temp,
    humidity: reading.humidity,
    sourceTopic: topic,
    deviceId: null, // Legacy readings don't have deviceId
  }).catch((err) => {
    console.error("Failed to store reading in Redis", err);
  });
}
