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
  const router = Router({mergeParams: true});

  // GET /api/devices/:deviceId/relays - List all relays from device actuators
  router.get("/", (req: Request, res: Response) => {
    try {
      const actuators = getDeviceActuators(String(req.params.deviceId));

      const relays = actuators.map((actuator) => ({
        id: actuator.id,
        name: actuator.customName ?? actuator.name ?? actuator.id,
        type: actuator.type ?? "switch",
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

  // GET /api/devices/:deviceId/relays/:id - Get a single relay by ID
  router.get("/:id", (req: Request, res: Response) => {
    try {
      const relayId = req.params.id as string;
      const actuators = getDeviceActuators(String(req.params.deviceId));
      const actuator = actuators.find((a) => a.id === relayId);

      if (!actuator) {
        res.status(404).json({ ok: false, error: "Relay not found" });
        return;
      }

      const relay = {
        id: actuator.id,
        name: actuator.customName ?? actuator.name ?? actuator.id,
        type: actuator.type ?? "switch",
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

  // POST /api/devices/:deviceId/relays/:id - Control relay state (toggle on/off or pulse)
  router.post("/:id", (req: Request, res: Response) => {
    try {
      const relayId = req.params.id as string;
      const targetDeviceId = String(req.params.deviceId);

      // Find the actuator from device capabilities
      const actuators = getDeviceActuators(targetDeviceId);
      const actuator = actuators.find((a) => a.id === relayId);

      if (!actuator) {
        res.status(404).json({ ok: false, error: "Relay not found" });
        return;
      }

      const relayName = actuator.customName ?? actuator.name ?? relayId;
      const targetLocation = actuator.location;

      if (actuator.type === "momentary") {
        // Momentary: send pulse action, no optimistic state update
        const correlationId = publishCommand({
          deviceId: targetDeviceId,
          location: targetLocation,
          target: relayId,
          action: "pulse",
          value: true,
          source: "dashboard",
          reason: `Relay ${relayName} pulsed via dashboard`,
        });

        if (!correlationId) {
          res.status(503).json({ ok: false, error: "MQTT client not connected" });
          return;
        }

        const command = insertCommand({
          id: correlationId,
          ts: Date.now(),
          deviceId: targetDeviceId,
          target: relayId,
          action: "pulse",
          value: true,
          source: "dashboard",
          reason: `Relay ${relayName} pulsed via dashboard`,
        });
        broadcastCommand(command);

        res.json({
          ok: true,
          correlationId,
          relay: {
            id: relayId,
            name: relayName,
            type: "momentary",
            state: false,
            deviceId: targetDeviceId,
            location: targetLocation,
          },
        });
      } else {
        // Switch: existing behavior
        const { state } = req.body;
        if (typeof state !== "boolean") {
          res.status(400).json({ ok: false, error: "state must be a boolean" });
          return;
        }

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
      }
    } catch (err) {
      console.error("Error setting relay state", err);
      res.status(500).json({ ok: false, error: "Failed to set relay state" });
    }
  });

  // PATCH /api/devices/:deviceId/relays/:id - Update relay name
  router.patch("/:id", (req: Request, res: Response) => {
    try {
      const { name } = req.body;
      const relayId = req.params.id as string;
      const targetDeviceId = String(req.params.deviceId);

      // Find the actuator to get its device
      const actuators = getDeviceActuators(targetDeviceId);
      const actuator = actuators.find((a) => a.id === relayId);

      if (!actuator) {
        res.status(404).json({ ok: false, error: "Relay not found" });
        return;
      }

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

  // DELETE /api/devices/:deviceId/relays/:id - Remove custom name (relay itself comes from device)
  router.delete("/:id", (req: Request, res: Response) => {
    try {
      const relayId = req.params.id as string;

      // Find the actuator to get its device
      const actuators = getDeviceActuators(String(req.params.deviceId));
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
