import mqtt from "mqtt";

import { config } from "../config/index.js";
import { parseTelemetry } from "../lib/telemetry.js";
import { setLatest } from "../state/latestReading.js";

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

      setLatest({
        temp: reading.temp,
        humidity: reading.humidity,
        updatedAt: Date.now(),
        sourceTopic: topic,
      });
    } catch {
      return;
    }
  });

  mqttClient.on("error", (err) => {
    console.error("MQTT error", err);
  });
}
