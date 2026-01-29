import mqtt from "mqtt";

import { config } from "../config/index.js";
import { parseTelemetry } from "../lib/telemetry.js";
import { storeReading } from "../lib/redis.js";
import { setLatest } from "../state/latestReading.js";
import { broadcastLatestReading } from "./websocket.js";

export function initMqttTelemetry(): void {
  const mqttClient = mqtt.connect(config.mqttUrl, {
    username: config.mqtt.username,
    password: config.mqtt.password,
    reconnectPeriod: config.mqtt.reconnectPeriod,
  });

  mqttClient.on("connect", () => {
    const topic = `${config.topicPrefix}/esp32-1/telemetry`;
    mqttClient.subscribe(topic, { qos: 0 }, (err) => {
      if (err) {
        console.error("MQTT subscribe error", err);
        return;
      }
      console.log(`MQTT connected: ${config.mqttUrl} (subscribed to ${topic})`);
    });
  });

  mqttClient.on("message", (topic, message) => {
    try {
      const text = message.toString("utf8");
      const json = JSON.parse(text) as unknown;
      console.log(json);
      const reading = parseTelemetry(json);
      if (!reading) return;

      const ts = Date.now();

      const latestReading = {
        temp: reading.temp,
        humidity: reading.humidity,
        updatedAt: ts,
        sourceTopic: topic,
      };

      setLatest(latestReading);

      // Broadcast to WebSocket clients
      broadcastLatestReading(latestReading);

      storeReading({
        ts,
        temp: reading.temp,
        humidity: reading.humidity,
        sourceTopic: topic,
      }).catch((err) => {
        console.error("Failed to store reading in Redis", err);
      });
    } catch(e) {
      console.error("Error", e);
      return;
    }
  });

  mqttClient.on("error", (err) => {
    console.error("MQTT error", err);
  });
}
