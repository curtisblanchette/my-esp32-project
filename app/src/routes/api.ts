import { Router, type Request, type Response } from "express";

import { queryHistoryBucketed, queryHistoryRaw } from "../lib/sqlite.js";
import { getLatest } from "../state/latestReading.js";

export function createApiRouter(): Router {
  const router = Router();

  router.get("/latest", (_req: Request, res: Response) => {
    res.json({ ok: true, latest: getLatest() });
  });

  router.get("/history", (req: Request, res: Response) => {
    const sinceMs = Number(req.query.sinceMs);
    const untilMs = req.query.untilMs === undefined ? Date.now() : Number(req.query.untilMs);
    const limit = req.query.limit === undefined ? 5000 : Number(req.query.limit);
    const bucketMs = req.query.bucketMs === undefined ? null : Number(req.query.bucketMs);

    if (!Number.isFinite(sinceMs)) {
      res.status(400).json({ ok: false, error: "sinceMs is required" });
      return;
    }
    if (!Number.isFinite(untilMs)) {
      res.status(400).json({ ok: false, error: "untilMs must be a number" });
      return;
    }
    if (!Number.isFinite(limit) || limit <= 0) {
      res.status(400).json({ ok: false, error: "limit must be a positive number" });
      return;
    }

    if (bucketMs !== null) {
      if (!Number.isFinite(bucketMs) || bucketMs <= 0) {
        res.status(400).json({ ok: false, error: "bucketMs must be a positive number" });
        return;
      }
      const points = queryHistoryBucketed({ sinceMs, untilMs, limit, bucketMs });
      res.json({ ok: true, mode: "bucketed", points });
      return;
    }

    const points = queryHistoryRaw({ sinceMs, untilMs, limit });
    res.json({ ok: true, mode: "raw", points });
  });

  return router;
}
