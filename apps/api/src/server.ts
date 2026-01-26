import { config } from "./config/index.js";
import { createApp } from "./app.js";
import { getDb } from "./lib/sqlite.js";
import { initMqttTelemetry } from "./services/mqttTelemetry.js";

getDb();

initMqttTelemetry();

const app = createApp();

app.listen(config.PORT, () => {
  console.log(`Server listening on http://localhost:${config.PORT}`);
});
