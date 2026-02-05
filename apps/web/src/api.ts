export type LatestReading = {
  temp: number;
  humidity: number;
  updatedAt: number;
  sourceTopic?: string;
  sourceIp?: string;
  deviceId?: string;
};

export type HistoryPoint = {
  ts: number;
  temp: number;
  humidity: number;
  count?: number;
};

export type RelayStatus = {
  id: string;
  name: string;
  state: boolean;
  updatedAt: number;
  deviceId?: string;
  location?: string;
  deviceOnline?: boolean;
};

export type Device = {
  id: string;
  location: string;
  name: string | null;
  platform: string | null;
  firmware: string | null;
  capabilities: {
    sensors: Array<{ id: string; type: string; name?: string }>;
    actuators: Array<{ id: string; type: string; pin?: number; name?: string }>;
  };
  telemetryIntervalMs: number | null;
  online: boolean;
  lastSeen: number;
  createdAt: number;
  updatedAt: number;
};

export type Command = {
  id: string;
  ts: number;
  deviceId: string;
  target: string;
  action: string;
  value: boolean | number | string;
  source: string;
  reason?: string;
  status?: "pending" | "acked" | "failed" | "expired";
  ackedAt?: number;
  actualValue?: boolean | number | string;
  error?: string;
};

export type DeviceEvent = {
  id: string;
  ts: number;
  deviceId: string;
  eventType: string;
  data?: Record<string, unknown>;
  source?: string;
};

export async function fetchLatest(signal?: AbortSignal): Promise<LatestReading | null> {
  const r = await fetch("/api/latest", { cache: "no-store", signal });
  const data = (await r.json()) as { ok: boolean; latest: LatestReading | null };
  return data.latest ?? null;
}

export async function fetchHistory(args: {
  sinceMs: number;
  untilMs: number;
  limit?: number;
  bucketMs?: number;
  deviceId?: string;
  signal?: AbortSignal;
}): Promise<HistoryPoint[]> {
  const limit = args.limit ?? 800;
  const bucketMs = args.bucketMs ?? 60_000;
  let url = `/api/history?sinceMs=${args.sinceMs}&untilMs=${args.untilMs}&limit=${limit}&bucketMs=${bucketMs}`;
  if (args.deviceId) {
    url += `&deviceId=${encodeURIComponent(args.deviceId)}`;
  }
  const r = await fetch(url, { cache: "no-store", signal: args.signal });
  const data = (await r.json()) as { ok: boolean; points: HistoryPoint[] };
  return Array.isArray(data.points) ? data.points : [];
}

export async function fetchRelayStatus(signal?: AbortSignal): Promise<RelayStatus[]> {
  const r = await fetch("/api/relays", { cache: "no-store", signal });
  const data = (await r.json()) as { ok: boolean; relays: RelayStatus[] };
  return Array.isArray(data.relays) ? data.relays : [];
}

export async function setRelayState(relayId: string, state: boolean): Promise<boolean> {
  const r = await fetch(`/api/relays/${relayId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state }),
  });
  const data = (await r.json()) as { ok: boolean };
  return data.ok;
}

export async function updateRelayName(relayId: string, name: string): Promise<boolean> {
  const r = await fetch(`/api/relays/${relayId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const data = (await r.json()) as { ok: boolean };
  return data.ok;
}

export async function fetchCommands(args: {
  sinceMs: number;
  untilMs?: number;
  deviceId?: string;
  status?: "pending" | "acked" | "failed" | "expired";
  limit?: number;
  signal?: AbortSignal;
}): Promise<Command[]> {
  const params = new URLSearchParams({ sinceMs: String(args.sinceMs) });
  if (args.untilMs) params.set("untilMs", String(args.untilMs));
  if (args.deviceId) params.set("deviceId", args.deviceId);
  if (args.status) params.set("status", args.status);
  if (args.limit) params.set("limit", String(args.limit));

  const r = await fetch(`/api/commands?${params}`, { cache: "no-store", signal: args.signal });
  const data = (await r.json()) as { ok: boolean; commands: Command[] };
  return Array.isArray(data.commands) ? data.commands : [];
}

export async function fetchEvents(args: {
  sinceMs: number;
  untilMs?: number;
  deviceId?: string;
  eventType?: string;
  limit?: number;
  signal?: AbortSignal;
}): Promise<DeviceEvent[]> {
  const params = new URLSearchParams({ sinceMs: String(args.sinceMs) });
  if (args.untilMs) params.set("untilMs", String(args.untilMs));
  if (args.deviceId) params.set("deviceId", args.deviceId);
  if (args.eventType) params.set("eventType", args.eventType);
  if (args.limit) params.set("limit", String(args.limit));

  const r = await fetch(`/api/events?${params}`, { cache: "no-store", signal: args.signal });
  const data = (await r.json()) as { ok: boolean; events: DeviceEvent[] };
  return Array.isArray(data.events) ? data.events : [];
}

// Chat types
export type ChatResponse = {
  ok: boolean;
  reply: string;
  action?: {
    type: "command" | "query" | "none";
    target?: string;
    sensor?: string;
    value?: boolean | number | string;
  };
};

export type StreamChatEvent =
  | { type: "token"; token: string }
  | { type: "done"; ok: boolean; reply: string; action?: ChatResponse["action"] }
  | { type: "error"; ok: false; reply: string };

export type VoiceHealthStatus = {
  ok: boolean;
  stt_available: boolean;
  tts_available: boolean;
  llm_available: boolean;
};

export type VoiceCommandResult = {
  ok: boolean;
  transcription: string;
  response: string;
  action?: string;
  target?: string;
  value?: boolean | number | string;
};

// Chat API
export async function checkChatHealth(): Promise<boolean> {
  try {
    const r = await fetch("/api/chat/health");
    const data = (await r.json()) as { ok: boolean };
    return data.ok;
  } catch {
    return false;
  }
}

export async function* sendChatStream(
  message: string,
  signal?: AbortSignal
): AsyncGenerator<StreamChatEvent> {
  const r = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
  });

  if (!r.ok) {
    yield { type: "error", ok: false, reply: "Failed to connect to chat service" };
    return;
  }

  const reader = r.body?.getReader();
  if (!reader) {
    yield { type: "error", ok: false, reply: "No response body" };
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const data = line.slice(6);
          if (data === "[DONE]") continue;
          try {
            const event = JSON.parse(data) as StreamChatEvent;
            yield event;
          } catch {
            // Skip malformed JSON
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// Voice API
export async function checkVoiceHealth(): Promise<VoiceHealthStatus | null> {
  try {
    const r = await fetch("/api/voice/health");
    const data = (await r.json()) as VoiceHealthStatus;
    return data;
  } catch {
    return null;
  }
}

export async function sendVoiceCommand(audioBlob: Blob): Promise<VoiceCommandResult> {
  const formData = new FormData();
  formData.append("audio", audioBlob, "recording.webm");

  const r = await fetch("/api/voice/command", {
    method: "POST",
    body: formData,
  });

  return (await r.json()) as VoiceCommandResult;
}

export async function synthesizeSpeech(text: string): Promise<Blob> {
  const r = await fetch("/api/voice/synthesize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: text }),
  });

  if (!r.ok) {
    throw new Error("Speech synthesis failed");
  }

  return r.blob();
}

// Device capability helpers
export function hasSensor(device: Device, type: string): boolean {
  return device.capabilities.sensors.some((s) => s.type === type);
}

export function hasTempHumiditySensors(device: Device): boolean {
  return hasSensor(device, "temperature") && hasSensor(device, "humidity");
}

export function hasActuators(device: Device): boolean {
  return device.capabilities.actuators.length > 0;
}
