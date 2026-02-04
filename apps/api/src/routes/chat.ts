import { Router, type Request, type Response } from "express";
import { insertCommand } from "../lib/sqlite.js";
import { getLatest } from "../state/latestReading.js";
import { publishCommand } from "../services/mqttTelemetry.js";
import { interpretMessage, interpretMessageStream, checkOllamaHealth } from "../services/ollama.js";
import { analyzeSensorData, formatAnalysisReply, fetchHistory, formatHistoryReply } from "./utils/analysis.js";

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

      // Handle different intents
      if (intent.intent === "command") {
        const targetDeviceId = deviceId ?? "esp32-1";
        const targetLocation = location ?? "room1";

        // Execute the command
        const correlationId = publishCommand({
          deviceId: targetDeviceId,
          location: targetLocation,
          target: intent.target,
          action: intent.action,
          value: intent.value,
          source: "chat",
          reason: `Chat command: "${message}"`,
        });

        if (!correlationId) {
          res.status(503).json({
            ok: false,
            reply: "I understood your request, but the device is currently unreachable.",
            error: "MQTT client not connected",
          });
          return;
        }

        // Store command in database
        insertCommand({
          id: correlationId,
          ts: Date.now(),
          deviceId: targetDeviceId,
          target: intent.target,
          action: intent.action,
          value: intent.value,
          source: "chat",
          reason: `Chat command: "${message}"`,
        });

        res.json({
          ok: true,
          reply: intent.reply,
          action: {
            type: "command",
            correlationId,
            target: intent.target,
            value: intent.value,
          },
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
          reply: intent.reply,
          action: {
            type: "query",
            sensor: intent.sensor,
            value: sensorValue,
          },
        });
        return;
      }

      if (intent.intent === "history") {
        const history = fetchHistory(intent);
        const formattedReply = formatHistoryReply(intent, history);

        res.json({
          ok: true,
          reply: formattedReply,
          action: {
            type: "history",
            timeframe: intent.timeframe,
            category: intent.category ?? "all",
            commands: history.commands,
            events: history.events,
          },
        });
        return;
      }

      if (intent.intent === "analyze") {
        try {
          const analysis = await analyzeSensorData(intent);
          const formattedReply = formatAnalysisReply(intent, analysis);

          res.json({
            ok: true,
            reply: formattedReply,
            action: {
              type: "analyze",
              timeframe: intent.timeframe,
              metric: intent.metric ?? "all",
              analysis,
            },
          });
        } catch (analysisError) {
          console.error("Error analyzing sensor data:", analysisError);
          res.json({
            ok: false,
            reply: "I encountered an error while analyzing the sensor data. Please try again.",
          });
        }
        return;
      }

      // intent === "none"
      res.json({
        ok: true,
        reply: intent.reply,
      });
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
          const intent = chunk.intent;

          // Handle different intents
          if (intent.intent === "command") {
            const targetDeviceId = deviceId ?? "esp32-1";
            const targetLocation = location ?? "room1";

            const correlationId = publishCommand({
              deviceId: targetDeviceId,
              location: targetLocation,
              target: intent.target,
              action: intent.action,
              value: intent.value,
              source: "chat",
              reason: `Chat command: "${message}"`,
            });

            if (correlationId) {
              insertCommand({
                id: correlationId,
                ts: Date.now(),
                deviceId: targetDeviceId,
                target: intent.target,
                action: intent.action,
                value: intent.value,
                source: "chat",
                reason: `Chat command: "${message}"`,
              });
            }

            res.write(
              `data: ${JSON.stringify({
                type: "done",
                ok: true,
                reply: intent.reply,
                action: {
                  type: "command",
                  correlationId,
                  target: intent.target,
                  value: intent.value,
                },
              })}\n\n`
            );
          } else if (intent.intent === "query") {
            const latest = getLatest();
            let sensorValue: number | null = null;

            if (latest) {
              if (intent.sensor === "temp1") {
                sensorValue = latest.temp;
              } else if (intent.sensor === "hum1") {
                sensorValue = latest.humidity;
              }
            }

            res.write(
              `data: ${JSON.stringify({
                type: "done",
                ok: true,
                reply: intent.reply,
                action: {
                  type: "query",
                  sensor: intent.sensor,
                  value: sensorValue,
                },
              })}\n\n`
            );
          } else if (intent.intent === "history") {
            const history = fetchHistory(intent);
            const formattedReply = formatHistoryReply(intent, history);

            res.write(
              `data: ${JSON.stringify({
                type: "done",
                ok: true,
                reply: formattedReply,
                action: {
                  type: "history",
                  timeframe: intent.timeframe,
                  category: intent.category ?? "all",
                  commands: history.commands,
                  events: history.events,
                },
              })}\n\n`
            );
          } else if (intent.intent === "analyze") {
            try {
              const analysis = await analyzeSensorData(intent);
              const formattedReply = formatAnalysisReply(intent, analysis);

              res.write(
                `data: ${JSON.stringify({
                  type: "done",
                  ok: true,
                  reply: formattedReply,
                  action: {
                    type: "analyze",
                    timeframe: intent.timeframe,
                    metric: intent.metric ?? "all",
                    analysis,
                  },
                })}\n\n`
              );
            } catch (analysisError) {
              console.error("Error analyzing sensor data:", analysisError);
              res.write(
                `data: ${JSON.stringify({
                  type: "done",
                  ok: false,
                  reply: "I encountered an error while analyzing the sensor data. Please try again.",
                })}\n\n`
              );
            }
          } else {
            res.write(
              `data: ${JSON.stringify({
                type: "done",
                ok: true,
                reply: intent.reply,
              })}\n\n`
            );
          }
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