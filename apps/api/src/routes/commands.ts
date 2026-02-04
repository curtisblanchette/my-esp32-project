import { Router, type Request, type Response } from "express";
import { insertCommand, getCommand, queryCommands } from "../lib/sqlite.js";
import { publishCommand } from "../services/mqttTelemetry.js";

export function createCommandsRouter(): Router {
  const router = Router();

  router.post("/", (req: Request, res: Response) => {
    try {
      const { deviceId, location, target, action, value, source, reason, ttl } = req.body;

      if (!deviceId || !location || !target || !action) {
        res.status(400).json({
          ok: false,
          error: "deviceId, location, target, and action are required",
        });
        return;
      }

      const correlationId = publishCommand({
        deviceId,
        location,
        target,
        action,
        value,
        source: source ?? "dashboard",
        reason,
        ttl,
      });

      if (!correlationId) {
        res.status(503).json({ ok: false, error: "MQTT client not connected" });
        return;
      }

      // Store command in database
      const command = insertCommand({
        id: correlationId,
        ts: Date.now(),
        deviceId,
        target,
        action,
        value,
        source: source ?? "dashboard",
        reason,
      });

      res.json({ ok: true, command });
    } catch (err) {
      console.error("Error sending command", err);
      res.status(500).json({ ok: false, error: "Failed to send command" });
    }
  });

  router.get("/", (req: Request, res: Response) => {
    try {
      const sinceMs = Number(req.query.sinceMs) || Date.now() - 24 * 60 * 60 * 1000;
      const untilMs = req.query.untilMs ? Number(req.query.untilMs) : undefined;
      const deviceId = req.query.deviceId as string | undefined;
      const status = req.query.status as "pending" | "acked" | "failed" | "expired" | undefined;
      const limit = req.query.limit ? Number(req.query.limit) : 100;

      const commands = queryCommands({ sinceMs, untilMs, deviceId, status, limit });
      res.json({ ok: true, commands });
    } catch (err) {
      console.error("Error querying commands", err);
      res.status(500).json({ ok: false, error: "Failed to query commands" });
    }
  });

  router.get("/:id", (req: Request, res: Response) => {
    try {
      const command = getCommand(req.params.id);
      if (!command) {
        res.status(404).json({ ok: false, error: "Command not found" });
        return;
      }
      res.json({ ok: true, command });
    } catch (err) {
      console.error("Error fetching command", err);
      res.status(500).json({ ok: false, error: "Failed to fetch command" });
    }
  });

  return router;
}