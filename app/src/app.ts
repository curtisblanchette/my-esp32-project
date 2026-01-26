import express from "express";
import path from "node:path";

import { createApiRouter } from "./routes/api.js";
import { createRootRouter } from "./routes/root.js";

export function createApp() {
  const app = express();

  app.use("/api", createApiRouter());

  app.use(
    express.static(path.resolve("public"), {
      fallthrough: true,
    })
  );

  app.use(createRootRouter());

  return app;
}
