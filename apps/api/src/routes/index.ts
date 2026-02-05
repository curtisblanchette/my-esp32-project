import { Router } from "express";
import { createTelemetryRouter } from "./telemetry.js";
import { createCommandsRouter } from "./commands.js";
import { createEventsRouter } from "./events.js";
import { createDevicesRouter } from "./devices.js";
import { createRelaysRouter } from "./relays.js";
import { createChatRouter } from "./chat.js";
import { createVoiceRouter } from "./voice.js";

export function createApiRouter(): Router {
  const router = Router();

  // Telemetry routes: /api/latest, /api/history
  router.use(createTelemetryRouter());

  // Commands routes: /api/commands
  router.use("/commands", createCommandsRouter());

  // Events routes: /api/events
  router.use("/events", createEventsRouter());

  // Devices routes: /api/devices
  router.use("/devices", createDevicesRouter());

  // Relays routes: /api/relays
  router.use("/relays", createRelaysRouter());

  // Chat routes: /api/chat
  router.use("/chat", createChatRouter());

  // Voice routes: /api/voice
  router.use("/voice", createVoiceRouter());

  return router;
}