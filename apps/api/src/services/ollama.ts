import { config } from "../config/index.js";
import { buildSystemPrompt } from "./systemPrompt.js";

export type OllamaIntent =
  | { intent: "command"; target: string; action: string; value: unknown; reply: string }
  | { intent: "query"; sensor: string; reply: string }
  | { intent: "history"; timeframe: string; category?: "commands" | "events" | "all"; reply: string; summary?: string }
  | { intent: "analyze"; timeframe: string; metric?: "temperature" | "humidity" | "all"; reply: string; summary?: string }
  | { intent: "none"; reply: string };

export async function interpretMessage(message: string): Promise<OllamaIntent> {
  const systemPrompt = buildSystemPrompt();

  const response = await fetch(`${config.ollama.url}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: config.ollama.model,
      prompt: message,
      system: systemPrompt,
      stream: false,
      format: "json",
    }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Ollama request failed: ${response.status} ${errorText}`);
  }

  const data = (await response.json()) as { response: string };

  try {
    const parsed = JSON.parse(data.response) as OllamaIntent;

    // Validate the response structure
    if (!parsed.intent || !parsed.reply) {
      throw new Error("Invalid response structure");
    }

    if (parsed.intent === "command") {
      if (!parsed.target || !parsed.action) {
        throw new Error("Command missing target or action");
      }
    }

    return parsed;
  } catch (parseError) {
    console.error("Failed to parse Ollama response:", data.response, parseError);
    return {
      intent: "none",
      reply: "I had trouble understanding that. Could you try rephrasing?",
    };
  }
}

/**
 * Stream interpretation of a message - yields partial tokens as they arrive.
 * Final yield contains the complete parsed intent.
 */
export async function* interpretMessageStream(
  message: string
): AsyncGenerator<{ type: "token"; token: string } | { type: "done"; intent: OllamaIntent }> {
  const systemPrompt = buildSystemPrompt();

  const response = await fetch(`${config.ollama.url}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: config.ollama.model,
      prompt: message,
      system: systemPrompt,
      stream: true,
      format: "json",
    }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Ollama request failed: ${response.status} ${errorText}`);
  }

  if (!response.body) {
    throw new Error("No response body for streaming");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let fullResponse = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      // Ollama streams newline-delimited JSON objects
      const lines = chunk.split("\n").filter((line) => line.trim());

      for (const line of lines) {
        try {
          const data = JSON.parse(line) as { response?: string; done?: boolean };
          if (data.response) {
            fullResponse += data.response;
            yield { type: "token", token: data.response };
          }
        } catch {
          // Skip malformed lines
        }
      }
    }
  } finally {
    reader.releaseLock();
  }

  // Parse the complete response
  try {
    console.log("Ollama raw response:", fullResponse);
    const parsed = JSON.parse(fullResponse) as OllamaIntent;
    console.log("Parsed intent:", parsed.intent);

    if (!parsed.intent || !parsed.reply) {
      throw new Error("Invalid response structure");
    }

    if (parsed.intent === "command") {
      if (!parsed.target || !parsed.action) {
        throw new Error("Command missing target or action");
      }
    }

    if (parsed.intent === "analyze") {
      if (!parsed.timeframe) {
        // Default timeframe if not provided
        (parsed as any).timeframe = "24h";
      }
    }

    if (parsed.intent === "history") {
      if (!parsed.timeframe) {
        (parsed as any).timeframe = "24h";
      }
    }

    yield { type: "done", intent: parsed };
  } catch (parseError) {
    console.error("Failed to parse Ollama response:", fullResponse, parseError);
    // If we can extract any text from the response, use it
    const fallbackReply = fullResponse.length > 0 && fullResponse.length < 500
      ? `I received: "${fullResponse.slice(0, 200)}..." but couldn't process it properly. Try asking more specifically, like "analyze temperature for the last 24 hours".`
      : "I had trouble understanding that. Try asking something like 'analyze temperature spikes in the last 24 hours'.";
    yield {
      type: "done",
      intent: {
        intent: "none",
        reply: fallbackReply,
      },
    };
  }
}

export async function checkOllamaHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${config.ollama.url}/api/tags`, {
      method: "GET",
      signal: AbortSignal.timeout(5000),
    });
    return response.ok;
  } catch {
    return false;
  }
}
