import express from "express";

import { createApiRouter } from "./routes/api.js";
import { createRootRouter } from "./routes/root.js";

export function createApp() {
  const app = express();

  app.use("/api", createApiRouter());
  app.use(createRootRouter());

  return app;
}
