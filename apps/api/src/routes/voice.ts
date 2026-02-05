import { Router, type Request, type Response, type RequestHandler } from 'express';
import multer from "multer";
import { interpretMessage } from "../services/ollama.js";
import { insertCommand } from "../lib/sqlite.js";
import { getLatest } from "../state/latestReading.js";
import { publishCommand } from "../services/mqttTelemetry.js";
import { broadcastCommand } from "../services/websocket.js";

const AI_SERVICE_URL = process.env.AI_SERVICE_URL || "http://ai:8000";

// Configure multer for memory storage (audio files)
const upload = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: 10 * 1024 * 1024, // 10MB max
  },
});

export function createVoiceRouter(): Router {
  const router = Router();

  // Transcribe audio to text
  router.post("/transcribe", upload.single("audio"), async (req: Request, res: Response) => {
    try {
      if (!req.file) {
        res.status(400).json({ ok: false, error: "No audio file provided" });
        return;
      }

      const formData = new FormData();
      formData.append("audio", new Blob([new Uint8Array(req.file.buffer)]), req.file.originalname || "audio.wav");

      const response = await fetch(`${AI_SERVICE_URL}/voice/transcribe`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const error = await response.text();
        res.status(response.status).json({ ok: false, error });
        return;
      }

      const data = await response.json();
      res.json({ ok: true, ...data });
    } catch (err) {
      console.error("Error transcribing audio:", err);
      res.status(503).json({
        ok: false,
        error: "Voice service unavailable",
      });
    }
  });

  // Synthesize text to speech
  router.post("/synthesize", async (req: Request, res: Response) => {
    try {
      const { message } = req.body;

      if (!message || typeof message !== "string") {
        res.status(400).json({ ok: false, error: "message is required" });
        return;
      }

      const response = await fetch(`${AI_SERVICE_URL}/voice/synthesize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });

      if (!response.ok) {
        const error = await response.text();
        res.status(response.status).json({ ok: false, error });
        return;
      }

      // Stream the audio response
      res.setHeader("Content-Type", "audio/wav");
      const arrayBuffer = await response.arrayBuffer();
      res.send(Buffer.from(arrayBuffer));
    } catch (err) {
      console.error("Error synthesizing speech:", err);
      res.status(503).json({
        ok: false,
        error: "Voice service unavailable",
      });
    }
  });

  // Process voice command end-to-end (audio in, JSON out)
  // Uses AI service for transcription, then API's orchestrator for intent processing
  router.post("/command", upload.single("audio"), async (req: Request, res: Response) => {
    try {
      if (!req.file) {
        res.status(400).json({ ok: false, error: "No audio file provided" });
        return;
      }

      // Step 1: Transcribe using AI service
      const formData = new FormData();
      formData.append("audio", new Blob([new Uint8Array(req.file.buffer)]), req.file.originalname || "audio.wav");

      const transcribeResponse = await fetch(`${AI_SERVICE_URL}/voice/transcribe`, {
        method: "POST",
        body: formData,
      });

      if (!transcribeResponse.ok) {
        const error = await transcribeResponse.text();
        res.status(transcribeResponse.status).json({ ok: false, error });
        return;
      }

      const transcription = (await transcribeResponse.json()) as { text: string; success: boolean };

      if (!transcription.text) {
        res.json({
          ok: true,
          transcription: "",
          response: "I didn't catch that. Could you please repeat?",
        });
        return;
      }

      // Step 2: Process with API's orchestrator (same as chat)
      const intent = await interpretMessage(transcription.text);

      // Step 3: Handle different intents
      if (intent.intent === "command") {
        const deviceId = (req.body.deviceId as string) ?? "esp32-1";
        const location = (req.body.location as string) ?? "room1";

        const correlationId = publishCommand({
          deviceId,
          location,
          target: intent.target,
          action: intent.action,
          value: intent.value,
          source: "voice",
          reason: `Voice command: "${transcription.text}"`,
        });

        if (correlationId) {
          const command = insertCommand({
            id: correlationId,
            ts: Date.now(),
            deviceId,
            target: intent.target,
            action: intent.action,
            value: intent.value,
            source: "voice",
            reason: `Voice command: "${transcription.text}"`,
          });
          broadcastCommand(command);
        }

        res.json({
          ok: true,
          transcription: transcription.text,
          response: intent.reply,
          action: "command",
          target: intent.target,
          value: intent.value,
        });
        return;
      }

      if (intent.intent === "query") {
        const latest = getLatest();
        let sensorValue: number | null = null;

        if (latest) {
          if (intent.sensor === "temp1") {
            sensorValue = latest.temp;
          } else if (intent.sensor === "hum1") {
            sensorValue = latest.humidity;
          }
        }

        res.json({
          ok: true,
          transcription: transcription.text,
          response: intent.reply,
          action: "query",
          target: intent.sensor,
          value: sensorValue,
        });
        return;
      }

      // For history/analyze/none intents
      res.json({
        ok: true,
        transcription: transcription.text,
        response: intent.reply,
      });
    } catch (err) {
      console.error("Error processing voice command:", err);
      res.status(503).json({
        ok: false,
        error: "Voice service unavailable",
      });
    }
  });

  // Process voice command with audio response
  // Uses AI service for transcription + TTS, API's orchestrator for intent processing
  router.post("/command/audio", upload.single("audio"), async (req: Request, res: Response) => {
    try {
      if (!req.file) {
        res.status(400).json({ ok: false, error: "No audio file provided" });
        return;
      }

      // Step 1: Transcribe using AI service
      const formData = new FormData();
      formData.append("audio", new Blob([new Uint8Array(req.file.buffer)]), req.file.originalname || "audio.wav");

      const transcribeResponse = await fetch(`${AI_SERVICE_URL}/voice/transcribe`, {
        method: "POST",
        body: formData,
      });

      if (!transcribeResponse.ok) {
        const error = await transcribeResponse.text();
        res.status(transcribeResponse.status).json({ ok: false, error });
        return;
      }

      const transcription = (await transcribeResponse.json()) as { text: string; success: boolean };
      let responseText: string;

      if (!transcription.text) {
        responseText = "I didn't catch that. Could you please repeat?";
      } else {
        // Step 2: Process with API's orchestrator
        const intent = await interpretMessage(transcription.text);

        // Step 3: Handle different intents
        if (intent.intent === "command") {
          const deviceId = (req.body.deviceId as string) ?? "esp32-1";
          const location = (req.body.location as string) ?? "room1";

          const correlationId = publishCommand({
            deviceId,
            location,
            target: intent.target,
            action: intent.action,
            value: intent.value,
            source: "voice",
            reason: `Voice command: "${transcription.text}"`,
          });

          if (correlationId) {
            const command = insertCommand({
              id: correlationId,
              ts: Date.now(),
              deviceId,
              target: intent.target,
              action: intent.action,
              value: intent.value,
              source: "voice",
              reason: `Voice command: "${transcription.text}"`,
            });
            broadcastCommand(command);
          }
        }

        responseText = intent.reply;
      }

      // Step 4: Synthesize response audio
      const synthesizeResponse = await fetch(`${AI_SERVICE_URL}/voice/synthesize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: responseText }),
      });

      if (!synthesizeResponse.ok) {
        res.status(503).json({ ok: false, error: "TTS service unavailable" });
        return;
      }

      res.setHeader("Content-Type", "audio/wav");
      const arrayBuffer = await synthesizeResponse.arrayBuffer();
      res.send(Buffer.from(arrayBuffer));
    } catch (err) {
      console.error("Error processing voice command with audio:", err);
      res.status(503).json({
        ok: false,
        error: "Voice service unavailable",
      });
    }
  });

  // Health check for voice service
  router.get("/health", async (_req: Request, res: Response) => {
    try {
      const response = await fetch(`${AI_SERVICE_URL}/health`);
      if (!response.ok) {
        res.json({ ok: false, service: "voice" });
        return;
      }
      const data = await response.json();
      res.json({ ok: true, service: "voice", ...data });
    } catch (err) {
      res.json({ ok: false, service: "voice", error: "Service unavailable" });
    }
  });

  return router;
}
