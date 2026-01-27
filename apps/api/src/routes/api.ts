import { Router, type Request, type Response } from "express";

import { queryHistoryBucketed, queryHistoryRaw } from "../lib/sqlite.js";
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
      const sqliteReadings = queryHistoryRaw({ sinceMs, untilMs, limit });

      const allReadings = [...sqliteReadings, ...redisReadings];
      allReadings.sort((a, b) => a.ts - b.ts);

      const limitedReadings = allReadings.slice(0, limit);

      if (bucketMs !== null) {
        if (!Number.isFinite(bucketMs) || bucketMs <= 0) {
          res.status(400).json({ ok: false, error: "bucketMs must be a positive number" });
          return;
        }

        const buckets = new Map<number, { tempSum: number; humiditySum: number; count: number }>();
        for (const reading of limitedReadings) {
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

        res.json({ ok: true, mode: "bucketed", points, sources: { redis: redisReadings.length, sqlite: sqliteReadings.length } });
        return;
      }

      res.json({ ok: true, mode: "raw", points: limitedReadings, sources: { redis: redisReadings.length, sqlite: sqliteReadings.length } });
    } catch (err) {
      console.error("Error querying history", err);
      res.status(500).json({ ok: false, error: "Failed to query history" });
    }
  });

  return router;
}
