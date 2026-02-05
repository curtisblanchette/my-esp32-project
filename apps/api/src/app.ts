import express, { type Request, type Response, type NextFunction } from "express";
import path from "node:path";

import { createApiRouter } from "./routes/index.js";
import { createRootRouter } from "./routes/root.js";

export function createApp() {
  const app = express();

  app.use(express.json());

  app.use("/api", createApiRouter());

  app.use(
    express.static(path.resolve("public"), {
      fallthrough: true,
    })
  );

  app.use(createRootRouter());

  app.use((req: Request, res: Response) => {
    console.error(`404 Not Found: ${req.method} ${req.url}`);
    res.status(404).json({ ok: false, error: "Not found" });
  });

  app.use((err: Error, req: Request, res: Response, _next: NextFunction) => {
    console.error("Unhandled error:", err);
    console.error("Request:", req.method, req.url);
    console.error("Stack:", err.stack);
    res.status(500).json({ ok: false, error: "Internal server error" });
  });

  return app;
}
