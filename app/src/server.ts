import { config } from "./config/index.js";
import { createApp } from "./app.js";
import { initMqttTelemetry } from "./services/mqttTelemetry.js";

initMqttTelemetry();

const app = createApp();

app.listen(config.PORT, () => {
  console.log(`Server listening on http://localhost:${config.PORT}`);
});
