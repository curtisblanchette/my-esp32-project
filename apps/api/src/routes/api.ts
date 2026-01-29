import { Router, type Request, type Response } from "express";

import { queryHistoryBucketed, queryHistoryRaw, getAllRelayConfigs, getRelayConfig, createRelayConfig, updateRelayConfig, deleteRelayConfig } from "../lib/sqlite.js";
import { getReadingsInRange } from "../lib/redis.js";
import { getLatest } from "../state/latestReading.js";

export function createApiRouter(): Router {
  const router = Router();

  router.get("/latest", (_req: Request, res: Response) => {
    res.json({ ok: true, latest: getLatest() });
  });

  router.get("/history", async (req: Request, res: Response) => {
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

    try {
      const redisReadings = await getReadingsInRange(sinceMs, untilMs);

      if (bucketMs !== null) {
        if (!Number.isFinite(bucketMs) || bucketMs <= 0) {
          res.status(400).json({ ok: false, error: "bucketMs must be a positive number" });
          return;
        }

        const bucketedSqlite = queryHistoryBucketed({ sinceMs, untilMs, limit, bucketMs });

        const buckets = new Map<number, { tempSum: number; humiditySum: number; count: number }>();

        for (const row of bucketedSqlite) {
          buckets.set(row.ts, {
            tempSum: row.temp * row.count,
            humiditySum: row.humidity * row.count,
            count: row.count,
          });
        }

        for (const reading of redisReadings) {
          const bucketTs = Math.floor(reading.ts / bucketMs) * bucketMs;
          const existing = buckets.get(bucketTs);
          if (existing) {
            existing.tempSum += reading.temp;
            existing.humiditySum += reading.humidity;
            existing.count += 1;
          } else {
            buckets.set(bucketTs, {
              tempSum: reading.temp,
              humiditySum: reading.humidity,
              count: 1,
            });
          }
        }

        const points = Array.from(buckets.entries()).map(([ts, bucket]) => ({
          ts,
          temp: bucket.tempSum / bucket.count,
          humidity: bucket.humiditySum / bucket.count,
          count: bucket.count,
        }));
        points.sort((a, b) => a.ts - b.ts);

        const limitedPoints = points.slice(-limit);

        res.json({ ok: true, mode: "bucketed", points: limitedPoints, sources: { redis: redisReadings.length, sqlite: bucketedSqlite.length } });
        return;
      }

      const sqliteReadings = queryHistoryRaw({ sinceMs, untilMs, limit });
      const allReadings = [...sqliteReadings, ...redisReadings];
      allReadings.sort((a, b) => a.ts - b.ts);
      const limitedReadings = allReadings.slice(-limit);

      res.json({ ok: true, mode: "raw", points: limitedReadings, sources: { redis: redisReadings.length, sqlite: sqliteReadings.length } });
    } catch (err) {
      console.error("Error querying history", err);
      res.status(500).json({ ok: false, error: "Failed to query history" });
    }
  });

  router.get("/relays", (_req: Request, res: Response) => {
    try {
      const configs = getAllRelayConfigs();
      const relays = configs.map((config) => ({
        id: config.id,
        name: config.name,
        state: Boolean(config.enabled),
        updatedAt: config.updatedAt,
      }));
      res.json({ ok: true, relays });
    } catch (err) {
      console.error("Error fetching relays", err);
      res.status(500).json({ ok: false, error: "Failed to fetch relays" });
    }
  });

  router.get("/relays/:id", (req: Request, res: Response) => {
    try {
      const relay = getRelayConfig(req.params.id);
      if (!relay) {
        res.status(404).json({ ok: false, error: "Relay not found" });
        return;
      }
      res.json({ ok: true, relay });
    } catch (err) {
      console.error("Error fetching relay", err);
      res.status(500).json({ ok: false, error: "Failed to fetch relay" });
    }
  });

  router.post("/relays", (req: Request, res: Response) => {
    try {
      const { id, name, pin, enabled } = req.body;
      if (!id || !name) {
        res.status(400).json({ ok: false, error: "id and name are required" });
        return;
      }
      const relay = createRelayConfig({ id, name, pin, enabled });
      res.json({ ok: true, relay });
    } catch (err) {
      console.error("Error creating relay", err);
      res.status(500).json({ ok: false, error: "Failed to create relay" });
    }
  });

  router.patch("/relays/:id", (req: Request, res: Response) => {
    try {
      const { name, pin, enabled } = req.body;
      const relay = updateRelayConfig(req.params.id, { name, pin, enabled });
      if (!relay) {
        res.status(404).json({ ok: false, error: "Relay not found" });
        return;
      }
      res.json({ ok: true, relay });
    } catch (err) {
      console.error("Error updating relay", err);
      res.status(500).json({ ok: false, error: "Failed to update relay" });
    }
  });

  router.post("/relays/:id", (req: Request, res: Response) => {
    try {
      const { state } = req.body;
      if (typeof state !== "boolean") {
        res.status(400).json({ ok: false, error: "state must be a boolean" });
        return;
      }
      res.json({ ok: true });
    } catch (err) {
      console.error("Error setting relay state", err);
      res.status(500).json({ ok: false, error: "Failed to set relay state" });
    }
  });

  router.delete("/relays/:id", (req: Request, res: Response) => {
    try {
      const success = deleteRelayConfig(req.params.id);
      if (!success) {
        res.status(404).json({ ok: false, error: "Relay not found" });
        return;
      }
      res.json({ ok: true });
    } catch (err) {
      console.error("Error deleting relay", err);
      res.status(500).json({ ok: false, error: "Failed to delete relay" });
    }
  });

  return router;
}
