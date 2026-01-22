import { Router, type Request, type Response } from "express";

import { getLatest } from "../state/latestReading.js";

export function createApiRouter(): Router {
  const router = Router();

  router.get("/latest", (_req: Request, res: Response) => {
    res.json({ ok: true, latest: getLatest() });
  });

  return router;
}
