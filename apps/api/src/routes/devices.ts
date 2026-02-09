import { Router, type Request, type Response } from "express";
import { getAllDevices, getDevice, getDeviceActuators, reorderDevices } from "../lib/sqlite.js";
import { broadcastDevices } from "../services/websocket.js";

export function createDevicesRouter(): Router {
  const router = Router();

  // Reorder devices — placed before /:id to avoid param conflict
  router.put("/order", (req: Request, res: Response) => {
    try {
      const { order } = req.body as { order: string[] };
      if (!Array.isArray(order)) {
        res.status(400).json({ ok: false, error: "order must be an array of device IDs" });
        return;
      }
      const orders = order.map((id, index) => ({ id, order: index }));
      reorderDevices(orders);
      broadcastDevices();
      res.json({ ok: true });
    } catch (err) {
      console.error("Error reordering devices", err);
      res.status(500).json({ ok: false, error: "Failed to reorder devices" });
    }
  });

  router.get("/", (_req: Request, res: Response) => {
    try {
      const devices = getAllDevices();
      res.json({ ok: true, devices });
    } catch (err) {
      console.error("Error fetching devices", err);
      res.status(500).json({ ok: false, error: "Failed to fetch devices" });
    }
  });

  router.get("/:id", (req: Request, res: Response) => {
    try {
      const device = getDevice(String(req.params.id));
      if (!device) {
        res.status(404).json({ ok: false, error: "Device not found" });
        return;
      }
      res.json({ ok: true, device });
    } catch (err) {
      console.error("Error fetching device", err);
      res.status(500).json({ ok: false, error: "Failed to fetch device" });
    }
  });

  router.get("/:id/actuators", (req: Request, res: Response) => {
    try {
      const device = getDevice(String(req.params.id));
      if (!device) {
        res.status(404).json({ ok: false, error: "Device not found" });
        return;
      }
      const actuators = getDeviceActuators(String(req.params.id));
      res.json({ ok: true, actuators });
    } catch (err) {
      console.error("Error fetching device actuators", err);
      res.status(500).json({ ok: false, error: "Failed to fetch device actuators" });
    }
  });

  return router;
}