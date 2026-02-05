import { createServer } from "http";
import { config } from "./config/index.js";
import { createApp } from "./app.js";
import { getDb } from "./lib/sqlite.js";
import { initMqttTelemetry } from "./services/mqttTelemetry.js";
import { startAggregationJob } from "./services/aggregationJob.js";
import { startCommandExpirationJob } from "./services/commandExpirationJob.js";
import { createWebSocketServer } from "./services/websocket.js";

getDb();

initMqttTelemetry();

startAggregationJob();
startCommandExpirationJob();

const app = createApp();
const server = createServer(app);

createWebSocketServer(server);

server.listen(config.PORT, () => {
  console.log(`Server listening on http://localhost:${config.PORT}`);
  console.log(`WebSocket server available at ws://localhost:${config.PORT}/ws`);
});
