import "dotenv/config";

import express, { type Request, type Response } from "express";
import mqtt from "mqtt";

type SensorReading = {
  temp: number;
  humidity: number;
  updatedAt: number;
  sourceIp?: string;
  sourceTopic?: string;
};

const app = express();

const PORT = Number(process.env.PORT ?? 3000);

const MQTT_URL = process.env.MQTT_URL;
const MQTT_HOST = process.env.MQTT_HOST ?? "localhost";
const MQTT_PORT = Number(process.env.MQTT_PORT ?? 1883);
const MQTT_USERNAME = process.env.MQTT_USERNAME;
const MQTT_PASSWORD = process.env.MQTT_PASSWORD;
const MQTT_TOPIC_PREFIX = process.env.MQTT_TOPIC_PREFIX ?? "/device";

const mqttUrl = MQTT_URL ?? `mqtt://${MQTT_HOST}:${MQTT_PORT}`;

let latest: SensorReading | null = null;

function parseNumber(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return n;
}

function parseTelemetry(payload: unknown): { temp: number; humidity: number } | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;

  const tempRaw = p.temp ?? p.tempC ?? p.temperature ?? p.temperatureC;
  const humidityRaw = p.humidity ?? p.hum;

  const temp = typeof tempRaw === "number" ? tempRaw : typeof tempRaw === "string" ? Number(tempRaw) : NaN;
  const humidity =
    typeof humidityRaw === "number" ? humidityRaw : typeof humidityRaw === "string" ? Number(humidityRaw) : NaN;

  if (!Number.isFinite(temp) || !Number.isFinite(humidity)) return null;
  return { temp, humidity };
}

const mqttClient = mqtt.connect(mqttUrl, {
  username: MQTT_USERNAME,
  password: MQTT_PASSWORD,
  reconnectPeriod: 1000,
});

mqttClient.on("connect", () => {
  const topic = `${MQTT_TOPIC_PREFIX}/esp32-1/telemetry`;
  mqttClient.subscribe(topic, { qos: 0 }, (err) => {
    if (err) {
      console.error("MQTT subscribe error", err);
      return;
    }
    console.log(`MQTT connected: ${mqttUrl} (subscribed to ${topic})`);
  });
});

mqttClient.on("message", (topic, message) => {
  try {
    const text = message.toString("utf8");
    const json = JSON.parse(text) as unknown;
    console.log(json);
    const reading = parseTelemetry(json);
    if (!reading) return;

    latest = {
      temp: reading.temp,
      humidity: reading.humidity,
      updatedAt: Date.now(),
      sourceTopic: topic,
    };
  } catch {
    return;
  }
});

mqttClient.on("error", (err) => {
  console.error("MQTT error", err);
});

app.get("/api/latest", (_req: Request, res: Response) => {
  res.json({ ok: true, latest });
});

app.get("/", (_req: Request, res: Response) => {
  res.type("html");
  res.send(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Sensor Dashboard</title>
    <style>
      :root {
        color-scheme: light dark;
      }
      body {
        font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji",
          "Segoe UI Emoji";
        margin: 0;
        padding: 24px;
        display: grid;
        place-items: center;
      }
      .card {
        width: min(720px, 100%);
        border: 1px solid rgba(127, 127, 127, 0.3);
        border-radius: 16px;
        padding: 20px;
      }
      h1 {
        margin: 0 0 12px;
        font-size: 20px;
      }
      .grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
        margin-top: 12px;
      }
      .metric {
        padding: 16px;
        border-radius: 12px;
        border: 1px solid rgba(127, 127, 127, 0.25);
      }
      .label {
        opacity: 0.75;
        font-size: 12px;
        margin-bottom: 6px;
      }
      .value {
        font-size: 28px;
        font-weight: 700;
      }
      .meta {
        margin-top: 12px;
        font-size: 12px;
        opacity: 0.75;
      }
      code {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>Sensor Dashboard</h1>
      <div class="grid">
        <div class="metric">
          <div class="label">Temperature</div>
          <div class="value" id="temp">--</div>
        </div>
        <div class="metric">
          <div class="label">Humidity</div>
          <div class="value" id="humidity">--</div>
        </div>
      </div>
      <div class="meta" id="meta">Waiting for first reading...</div>
    </div>

    <script>
      const tempEl = document.getElementById('temp');
      const humidityEl = document.getElementById('humidity');
      const metaEl = document.getElementById('meta');

      function fmtTime(ms) {
        const d = new Date(ms);
        return d.toLocaleString();
      }

      async function refresh() {
        try {
          const r = await fetch('/api/latest', { cache: 'no-store' });
          const data = await r.json();
          const latest = data.latest;

          if (!latest) {
            tempEl.textContent = '--';
            humidityEl.textContent = '--';
            metaEl.textContent = 'Waiting for first reading...';
            return;
          }

          tempEl.textContent = String(latest.temp);
          humidityEl.textContent = String(latest.humidity);
          metaEl.textContent =
            'Last update: ' +
            fmtTime(latest.updatedAt) +
            (latest.sourceIp ? ' | Source: ' + latest.sourceIp : '');
        } catch (e) {
          metaEl.textContent = 'Error fetching latest reading.';
        }
      }

      refresh();
      setInterval(refresh, 2000);
    </script>
  </body>
</html>`);
});

app.listen(PORT, () => {
  console.log(`Server listening on http://localhost:${PORT}`);
});
