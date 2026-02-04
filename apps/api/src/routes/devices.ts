import { Router, type Request, type Response } from "express";
import { getAllDevices, getDevice, getDeviceActuators } from "../lib/sqlite.js";

export function createDevicesRouter(): Router {
  const router = Router();

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
      const device = getDevice(req.params.id);
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
      const device = getDevice(req.params.id);
      if (!device) {
        res.status(404).json({ ok: false, error: "Device not found" });
        return;
      }
      const actuators = getDeviceActuators(req.params.id);
      res.json({ ok: true, actuators });
    } catch (err) {
      console.error("Error fetching device actuators", err);
      res.status(500).json({ ok: false, error: "Failed to fetch device actuators" });
    }
  });

  return router;
}