import { Router, type Request, type Response } from "express";
import {
  getAllRelayConfigs,
  getRelayConfig,
  createRelayConfig,
  updateRelayConfig,
  deleteRelayConfig,
  insertCommand,
  getDeviceActuators,
} from "../lib/sqlite.js";
import { publishCommand } from "../services/mqttTelemetry.js";

export function createRelaysRouter(): Router {
  const router = Router();

  router.get("/", (_req: Request, res: Response) => {
    try {
      // Get actuators from all devices
      const actuators = getDeviceActuators();
      const configs = getAllRelayConfigs();
      const configMap = new Map(configs.map((c) => [c.id, c]));

      // Merge device actuators with user config (names, etc.)
      const relays = actuators.map((actuator) => {
        const config = configMap.get(actuator.id);
        return {
          id: actuator.id,
          name: config?.name ?? actuator.name ?? actuator.id,
          state: config?.enabled ?? false,
          updatedAt: config?.updatedAt ?? Date.now(),
          deviceId: actuator.deviceId,
          location: actuator.location,
          deviceOnline: actuator.deviceOnline,
        };
      });

      res.json({ ok: true, relays });
    } catch (err) {
      console.error("Error fetching relays", err);
      res.status(500).json({ ok: false, error: "Failed to fetch relays" });
    }
  });

  router.get("/:id", (req: Request, res: Response) => {
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

  router.post("/", (req: Request, res: Response) => {
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

  router.patch("/:id", (req: Request, res: Response) => {
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

  router.post("/:id", (req: Request, res: Response) => {
    try {
      const { state, deviceId, location } = req.body;
      if (typeof state !== "boolean") {
        res.status(400).json({ ok: false, error: "state must be a boolean" });
        return;
      }

      const relayId = req.params.id;

      // Find the actuator from device capabilities
      const actuators = getDeviceActuators();
      const actuator = actuators.find((a) => a.id === relayId);

      // Get relay config for name (if exists)
      const relayConfig = getRelayConfig(relayId);
      const relayName = relayConfig?.name ?? actuator?.name ?? relayId;

      // Determine device info from actuator, request body, or defaults
      const targetDeviceId = deviceId ?? actuator?.deviceId ?? "esp32-1";
      const targetLocation = location ?? actuator?.location ?? "room1";

      if (!actuator && !relayConfig) {
        // Create relay config on-the-fly if actuator exists but no config yet
        // (This maintains backwards compatibility for creating relays)
        res.status(404).json({ ok: false, error: "Relay not found" });
        return;
      }

      // Send command to device via MQTT
      const correlationId = publishCommand({
        deviceId: targetDeviceId,
        location: targetLocation,
        target: relayId,
        action: "set",
        value: state,
        source: "dashboard",
        reason: `Relay ${relayName} set to ${state ? "ON" : "OFF"} via dashboard`,
      });

      if (!correlationId) {
        res.status(503).json({ ok: false, error: "MQTT client not connected" });
        return;
      }

      // Store command in database
      insertCommand({
        id: correlationId,
        ts: Date.now(),
        deviceId: targetDeviceId,
        target: relayId,
        action: "set",
        value: state,
        source: "dashboard",
        reason: `Relay ${relayName} set to ${state ? "ON" : "OFF"} via dashboard`,
      });

      // Auto-create or update relay config to track state
      if (relayConfig) {
        updateRelayConfig(relayId, { enabled: state });
      } else {
        createRelayConfig({ id: relayId, name: relayName, enabled: state });
      }

      res.json({
        ok: true,
        correlationId,
        relay: {
          id: relayId,
          name: relayName,
          state,
          deviceId: targetDeviceId,
          location: targetLocation,
        },
      });
    } catch (err) {
      console.error("Error setting relay state", err);
      res.status(500).json({ ok: false, error: "Failed to set relay state" });
    }
  });

  router.delete("/:id", (req: Request, res: Response) => {
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