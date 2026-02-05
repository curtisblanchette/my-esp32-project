import "dotenv/config";

const PORT = Number(process.env.PORT ?? 3000);

const SQLITE_PATH = process.env.SQLITE_PATH ?? "../../data/telemetry.sqlite";
const SQLITE_JOURNAL_MODE = process.env.SQLITE_JOURNAL_MODE ?? "WAL";

const MQTT_URL = process.env.MQTT_URL;
const MQTT_HOST = process.env.MQTT_HOST ?? "localhost";
const MQTT_PORT = Number(process.env.MQTT_PORT ?? 1883);
const MQTT_USERNAME = process.env.MQTT_USERNAME;
const MQTT_PASSWORD = process.env.MQTT_PASSWORD;
const MQTT_TOPIC_PREFIX = process.env.MQTT_TOPIC_PREFIX ?? "/device";

const REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379";

const OLLAMA_URL = process.env.OLLAMA_URL ?? "http://host.docker.internal:11434";
const OLLAMA_MODEL = process.env.OLLAMA_MODEL ?? "llama3.2:3b";

const mqttUrl = MQTT_URL ?? `mqtt://${MQTT_HOST}:${MQTT_PORT}`;

export const config = {
  PORT,
  sqlitePath: SQLITE_PATH,
  sqliteJournalMode: SQLITE_JOURNAL_MODE,
  mqttUrl,
  mqtt: {
    username: MQTT_USERNAME,
    password: MQTT_PASSWORD,
    reconnectPeriod: 1000,
  },
  topicPrefix: MQTT_TOPIC_PREFIX,
  redisUrl: REDIS_URL,
  ollama: {
    url: OLLAMA_URL,
    model: OLLAMA_MODEL,
  },
} as const;
