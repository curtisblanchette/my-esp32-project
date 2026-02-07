import type { OllamaIntent } from "../../services/ollama.js";
import { insertCommand } from "../../lib/sqlite.js";
import { getLatest } from "../../state/latestReading.js";
import { publishCommand } from "../../services/mqttTelemetry.js";
import { broadcastCommand } from "../../services/websocket.js";
import { analyzeSensorData, formatAnalysisReply, fetchHistory, formatHistoryReply } from "./analysis.js";

export type IntentContext = {
  deviceId?: string;
  location?: string;
  source: "chat" | "voice";
  message: string;
};

export type IntentResult = {
  ok: boolean;
  reply: string;
  action?: { type: string; [key: string]: unknown };
};

export async function executeIntent(
  intent: OllamaIntent,
  ctx: IntentContext
): Promise<IntentResult> {
  if (intent.intent === "command") {
    const deviceId = ctx.deviceId ?? "esp32-1";
    const location = ctx.location ?? "room1";

    const correlationId = publishCommand({
      deviceId,
      location,
      target: intent.target,
      action: intent.action,
      value: intent.value,
      source: ctx.source,
      reason: `${ctx.source === "voice" ? "Voice" : "Chat"} command: "${ctx.message}"`,
    });

    if (!correlationId) {
      return {
        ok: false,
        reply: "I understood your request, but the device is currently unreachable.",
      };
    }

    const command = insertCommand({
      id: correlationId,
      ts: Date.now(),
      deviceId,
      target: intent.target,
      action: intent.action,
      value: intent.value,
      source: ctx.source,
      reason: `${ctx.source === "voice" ? "Voice" : "Chat"} command: "${ctx.message}"`,
    });
    broadcastCommand(command);

    return {
      ok: true,
      reply: intent.reply,
      action: {
        type: "command",
        correlationId,
        target: intent.target,
        value: intent.value,
      },
    };
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

    return {
      ok: true,
      reply: intent.reply,
      action: {
        type: "query",
        sensor: intent.sensor,
        value: sensorValue,
      },
    };
  }

  if (intent.intent === "history") {
    const history = fetchHistory(intent);
    const formattedReply = formatHistoryReply(intent, history);

    return {
      ok: true,
      reply: formattedReply,
      action: {
        type: "history",
        timeframe: intent.timeframe,
        category: intent.category ?? "all",
        commands: history.commands,
        events: history.events,
      },
    };
  }

  if (intent.intent === "analyze") {
    try {
      const analysis = await analyzeSensorData(intent);
      const formattedReply = formatAnalysisReply(intent, analysis);

      return {
        ok: true,
        reply: formattedReply,
        action: {
          type: "analyze",
          timeframe: intent.timeframe,
          metric: intent.metric ?? "all",
          analysis,
        },
      };
    } catch (analysisError) {
      console.error("Error analyzing sensor data:", analysisError);
      return {
        ok: false,
        reply: "I encountered an error while analyzing the sensor data. Please try again.",
      };
    }
  }

  // intent === "none"
  return {
    ok: true,
    reply: intent.reply,
  };
}
