import "dotenv/config";

const PORT = Number(process.env.PORT ?? 3000);

const MQTT_URL = process.env.MQTT_URL;
const MQTT_HOST = process.env.MQTT_HOST ?? "localhost";
const MQTT_PORT = Number(process.env.MQTT_PORT ?? 1883);
const MQTT_USERNAME = process.env.MQTT_USERNAME;
const MQTT_PASSWORD = process.env.MQTT_PASSWORD;
const MQTT_TOPIC_PREFIX = process.env.MQTT_TOPIC_PREFIX ?? "/device";

const mqttUrl = MQTT_URL ?? `mqtt://${MQTT_HOST}:${MQTT_PORT}`;

export const config = {
  PORT,
  mqttUrl,
  mqtt: {
    username: MQTT_USERNAME,
    password: MQTT_PASSWORD,
    reconnectPeriod: 1000,
  },
  topicPrefix: MQTT_TOPIC_PREFIX,
} as const;
