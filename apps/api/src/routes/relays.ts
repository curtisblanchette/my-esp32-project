import { Router, type Request, type Response } from "express";
import {
  insertCommand,
  getDeviceActuators,
  getDevice,
  updateActuatorState,
  updateActuatorName,
  removeActuatorName,
} from "../lib/sqlite.js";
import { publishCommand } from "../services/mqttTelemetry.js";
import { broadcastCommand } from "../services/websocket.js";

export function createRelaysRouter(): Router {
  const router = Router();

  // GET /api/relays - List all relays from device actuators
  router.get("/", (_req: Request, res: Response) => {
    try {
      const actuators = getDeviceActuators();

      const relays = actuators.map((actuator) => ({
        id: actuator.id,
        name: actuator.customName ?? actuator.name ?? actuator.id,
        state: actuator.state ?? false,
        updatedAt: Date.now(),
        deviceId: actuator.deviceId,
        location: actuator.location,
        deviceOnline: actuator.deviceOnline,
      }));

      res.json({ ok: true, relays });
    } catch (err) {
      console.error("Error fetching relays", err);
      res.status(500).json({ ok: false, error: "Failed to fetch relays" });
    }
  });

  // GET /api/relays/:id - Get a single relay by ID
  router.get("/:id", (req: Request, res: Response) => {
    try {
      const relayId = req.params.id as string;
      const actuators = getDeviceActuators();
      const actuator = actuators.find((a) => a.id === relayId);

      if (!actuator) {
        res.status(404).json({ ok: false, error: "Relay not found" });
        return;
      }

      const relay = {
        id: actuator.id,
        name: actuator.customName ?? actuator.name ?? actuator.id,
        state: actuator.state ?? false,
        deviceId: actuator.deviceId,
        location: actuator.location,
        deviceOnline: actuator.deviceOnline,
      };

      res.json({ ok: true, relay });
    } catch (err) {
      console.error("Error fetching relay", err);
      res.status(500).json({ ok: false, error: "Failed to fetch relay" });
    }
  });

  // POST /api/relays/:id - Control relay state (toggle on/off)
  router.post("/:id", (req: Request, res: Response) => {
    try {
      const { state, deviceId, location } = req.body;
      if (typeof state !== "boolean") {
        res.status(400).json({ ok: false, error: "state must be a boolean" });
        return;
      }

      const relayId = req.params.id as string;

      // Find the actuator from device capabilities
      const actuators = getDeviceActuators();
      const actuator = actuators.find((a) => a.id === relayId);

      if (!actuator) {
        res.status(404).json({ ok: false, error: "Relay not found" });
        return;
      }

      const relayName = actuator.customName ?? actuator.name ?? relayId;

      // Determine device info from actuator or request body override
      const targetDeviceId = deviceId ?? actuator.deviceId;
      const targetLocation = location ?? actuator.location;

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

      // Store command in database and broadcast
      const command = insertCommand({
        id: correlationId,
        ts: Date.now(),
        deviceId: targetDeviceId,
        target: relayId,
        action: "set",
        value: state,
        source: "dashboard",
        reason: `Relay ${relayName} set to ${state ? "ON" : "OFF"} via dashboard`,
      });
      broadcastCommand(command);

      // Optimistically update actuator state in device record
      updateActuatorState(targetDeviceId, relayId, state);

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

  // PATCH /api/relays/:id - Update relay name
  router.patch("/:id", (req: Request, res: Response) => {
    try {
      const { name, deviceId } = req.body;
      const relayId = req.params.id as string;

      // Find the actuator to get its device
      const actuators = getDeviceActuators();
      const actuator = actuators.find((a) => a.id === relayId);

      if (!actuator) {
        res.status(404).json({ ok: false, error: "Relay not found" });
        return;
      }

      const targetDeviceId = deviceId ?? actuator.deviceId;

      if (name !== undefined) {
        const updated = updateActuatorName(targetDeviceId, relayId, name);
        if (!updated) {
          res.status(500).json({ ok: false, error: "Failed to update relay name" });
          return;
        }
      }

      // Return updated relay info
      const device = getDevice(targetDeviceId);
      const updatedActuator = device?.capabilities.actuators.find((a) => a.id === relayId);

      res.json({
        ok: true,
        relay: {
          id: relayId,
          name: device?.actuatorNames[relayId] ?? updatedActuator?.name ?? relayId,
          state: updatedActuator?.state ?? false,
          deviceId: targetDeviceId,
          location: device?.location ?? actuator.location,
          deviceOnline: device?.online ?? false,
        },
      });
    } catch (err) {
      console.error("Error updating relay", err);
      res.status(500).json({ ok: false, error: "Failed to update relay" });
    }
  });

  // DELETE /api/relays/:id - Remove custom name (relay itself comes from device)
  router.delete("/:id", (req: Request, res: Response) => {
    try {
      const relayId = req.params.id as string;

      // Find the actuator to get its device
      const actuators = getDeviceActuators();
      const actuator = actuators.find((a) => a.id === relayId);

      if (!actuator) {
        res.status(404).json({ ok: false, error: "Relay not found" });
        return;
      }

      // Remove custom name (actuator still exists from device capabilities)
      removeActuatorName(actuator.deviceId, relayId);

      res.json({ ok: true });
    } catch (err) {
      console.error("Error deleting relay", err);
      res.status(500).json({ ok: false, error: "Failed to delete relay" });
    }
  });

  return router;
}
