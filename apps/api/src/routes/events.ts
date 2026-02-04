import { Router, type Request, type Response } from "express";
import { queryEvents } from "../lib/sqlite.js";

export function createEventsRouter(): Router {
  const router = Router();

  router.get("/", (req: Request, res: Response) => {
    try {
      const sinceMs = Number(req.query.sinceMs) || Date.now() - 24 * 60 * 60 * 1000;
      const untilMs = req.query.untilMs ? Number(req.query.untilMs) : undefined;
      const deviceId = req.query.deviceId as string | undefined;
      const eventType = req.query.eventType as string | undefined;
      const limit = req.query.limit ? Number(req.query.limit) : 100;

      const events = queryEvents({ sinceMs, untilMs, deviceId, eventType, limit });
      res.json({ ok: true, events });
    } catch (err) {
      console.error("Error querying events", err);
      res.status(500).json({ ok: false, error: "Failed to query events" });
    }
  });

  return router;
}