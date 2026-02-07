import { Router, type Request, type Response } from "express";
import { interpretMessage, interpretMessageStream, checkOllamaHealth } from "../services/ollama.js";
import { executeIntent } from "./utils/executeIntent.js";

export function createChatRouter(): Router {
  const router = Router();

  // Chat endpoint for natural language commands
  router.post("/", async (req: Request, res: Response) => {
    try {
      const { message, deviceId, location } = req.body;

      if (!message || typeof message !== "string") {
        res.status(400).json({ ok: false, error: "message is required" });
        return;
      }

      // Interpret the message using Ollama
      const intent = await interpretMessage(message);

      const result = await executeIntent(intent, { deviceId, location, source: "chat", message });

      if (!result.ok && intent.intent === "command") {
        res.status(503).json({ ...result, error: "MQTT client not connected" });
        return;
      }

      res.json(result);
    } catch (err) {
      console.error("Error processing chat message", err);
      const errorMessage = err instanceof Error ? err.message : "Unknown error";

      // Check if it's an Ollama connection error
      if (errorMessage.includes("fetch failed") || errorMessage.includes("ECONNREFUSED")) {
        res.status(503).json({
          ok: false,
          error: "AI service unavailable",
          reply: "The AI assistant is currently unavailable. Please try again later.",
        });
        return;
      }

      res.status(500).json({
        ok: false,
        error: "Failed to process chat message",
        reply: "Something went wrong. Please try again.",
      });
    }
  });

  // Streaming chat endpoint - Server-Sent Events
  router.post("/stream", async (req: Request, res: Response) => {
    const { message, deviceId, location } = req.body;

    if (!message || typeof message !== "string") {
      res.status(400).json({ ok: false, error: "message is required" });
      return;
    }

    // Set up SSE headers
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.flushHeaders();

    try {
      // Stream tokens as they arrive
      for await (const chunk of interpretMessageStream(message)) {
        if (chunk.type === "token") {
          res.write(`data: ${JSON.stringify({ type: "token", token: chunk.token })}\n\n`);
        } else if (chunk.type === "done") {
          const result = await executeIntent(chunk.intent, { deviceId, location, source: "chat", message });
          res.write(`data: ${JSON.stringify({ type: "done", ...result })}\n\n`);
        }
      }
    } catch (err) {
      console.error("Error in streaming chat", err);
      const errorMessage = err instanceof Error ? err.message : "Unknown error";

      if (errorMessage.includes("fetch failed") || errorMessage.includes("ECONNREFUSED")) {
        res.write(
          `data: ${JSON.stringify({
            type: "error",
            error: "AI service unavailable",
            reply: "The AI assistant is currently unavailable. Please try again later.",
          })}\n\n`
        );
      } else {
        res.write(
          `data: ${JSON.stringify({
            type: "error",
            error: "Failed to process chat message",
            reply: "Something went wrong. Please try again.",
          })}\n\n`
        );
      }
    } finally {
      res.end();
    }
  });

  // Health check for Ollama
  router.get("/health", async (_req: Request, res: Response) => {
    const healthy = await checkOllamaHealth();
    res.json({ ok: healthy, service: "ollama" });
  });

  return router;
}
