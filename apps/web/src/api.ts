export type LatestReading = {
  readings: Record<string, number>;
  updatedAt: number;
  sourceTopic?: string;
  sourceIp?: string;
  deviceId?: string;
};

export type HistoryPoint = {
  ts: number;
  readings: Record<string, number>;
  count?: number;
};

export type RelayStatus = {
  id: string;
  name: string;
  type?: string;
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
    sensors: Array<{ id: string; type: string; name?: string; unit?: string }>;
    actuators: Array<{ id: string; type: string; pin?: number; name?: string }>;
  };
  telemetryIntervalMs: number | null;
  online: boolean;
  lastSeen: number;
  createdAt: number;
  updatedAt: number;
  displayOrder: number;
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

/** Normalize a latest reading from either old {temp,humidity} or new {readings:{...}} format. */
export function normalizeLatest(raw: Record<string, unknown> | null): LatestReading | null {
  if (!raw) return null;
  if (raw.readings && typeof raw.readings === "object") return raw as unknown as LatestReading;
  // Legacy format: {temp, humidity, updatedAt, ...} → {readings: {temp1: val, hum1: val}, ...}
  const readings: Record<string, number> = {};
  if (typeof raw.temp === "number") readings.temp1 = raw.temp;
  if (typeof raw.humidity === "number") readings.hum1 = raw.humidity;
  return { readings, updatedAt: raw.updatedAt as number, sourceTopic: raw.sourceTopic as string | undefined, deviceId: raw.deviceId as string | undefined };
}

/** Normalize a history point from either old {ts,temp,humidity} or new {ts,readings:{...}} format. */
function normalizeHistoryPoint(raw: Record<string, unknown>): HistoryPoint {
  if (raw.readings && typeof raw.readings === "object") return raw as unknown as HistoryPoint;
  const readings: Record<string, number> = {};
  if (typeof raw.temp === "number") readings.temp1 = raw.temp;
  if (typeof raw.humidity === "number") readings.hum1 = raw.humidity;
  return { ts: raw.ts as number, readings, count: raw.count as number | undefined };
}

export async function fetchLatest(signal?: AbortSignal): Promise<LatestReading | null> {
  const r = await fetch("/api/latest", { cache: "no-store", signal });
  const data = (await r.json()) as { ok: boolean; latest: Record<string, unknown> | null };
  return normalizeLatest(data.latest);
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
  const data = (await r.json()) as { ok: boolean; points: Record<string, unknown>[] };
  return Array.isArray(data.points) ? data.points.map(normalizeHistoryPoint) : [];
}

export async function fetchRelayStatus(deviceId: string, signal?: AbortSignal): Promise<RelayStatus[]> {
  const r = await fetch(`/api/devices/${deviceId}/relays`, { cache: "no-store", signal });
  const data = (await r.json()) as { ok: boolean; relays: RelayStatus[] };
  return Array.isArray(data.relays) ? data.relays : [];
}

export async function setRelayState(deviceId: string, relayId: string, state: boolean): Promise<boolean> {
  const r = await fetch(`/api/devices/${deviceId}/relays/${relayId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state }),
  });
  const data = (await r.json()) as { ok: boolean };
  return data.ok;
}

export async function pulseRelay(deviceId: string, relayId: string): Promise<boolean> {
  const r = await fetch(`/api/devices/${deviceId}/relays/${relayId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pulse: true }),
  });
  const data = (await r.json()) as { ok: boolean };
  return data.ok;
}

export async function updateRelayName(deviceId: string, relayId: string, name: string): Promise<boolean> {
  const r = await fetch(`/api/devices/${deviceId}/relays/${relayId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const data = (await r.json()) as { ok: boolean };
  return data.ok;
}

export async function saveDeviceOrder(order: string[]): Promise<boolean> {
  const r = await fetch("/api/devices/order", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order }),
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

// Observation categories for human-logged events
export const OBSERVATION_CATEGORIES = [
  // Plant health
  { key: "mold", label: "Mold" },
  { key: "powdery_mildew", label: "Powdery Mildew" },
  { key: "pests", label: "Pests" },
  { key: "needs_water", label: "Needs Water" },
  { key: "overwatered", label: "Overwatered" },
  { key: "nutrient_deficiency", label: "Nutrient Deficiency" },
  { key: "wilting", label: "Wilting" },
  { key: "leaf_damage", label: "Leaf Damage" },
  { key: "root_rot", label: "Root Rot" },
  { key: "harvest_ready", label: "Harvest Ready" },
  // Equipment / maintenance
  { key: "filter_changed", label: "Filter Changed" },
  { key: "sensor_replaced", label: "Sensor Replaced" },
  { key: "device_moved", label: "Device Moved" },
  // Utility
  { key: "high_bill_power", label: "High Power Bill" },
  { key: "high_bill_gas", label: "High Gas Bill" },
  // Catch-all
  { key: "general", label: "General Note" },
] as const;

export type ObservationCategory = (typeof OBSERVATION_CATEGORIES)[number]["key"];

export async function logObservation(args: {
  deviceId: string;
  category: ObservationCategory;
  notes?: string;
}): Promise<{ ok: boolean; event?: DeviceEvent }> {
  const r = await fetch("/api/observations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  return (await r.json()) as { ok: boolean; event?: DeviceEvent };
}

// Chat types
export type ProposedRule = {
  name: string;
  description: string;
  condition: Record<string, unknown>;
  action: Record<string, unknown>;
};

export type ChatResponse = {
  ok: boolean;
  reply: string;
  action?: {
    type: "command" | "query" | "none" | "proposed_rules" | "rules_activated";
    target?: string;
    sensor?: string;
    value?: boolean | number | string;
    rules?: ProposedRule[];
    count?: number;
    location?: string;
    goal?: string;
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
  signal?: AbortSignal,
  sessionId?: string,
): AsyncGenerator<StreamChatEvent> {
  const r = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, sessionId }),
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

// ── Cortex Intelligence API ────────────────────────────────────────

export type CortexStatus = {
  ok: boolean;
  outcomes: { total: number; avgEffectiveness: number; successRate: number };
  baselines: { total: number; sensorsTracked: number };
  suggestions: { pending: number; applied: number; rejected: number; total: number };
  lastAdvisorRun: number | null;
};

export type RuleSuggestion = {
  id: string;
  createdAt: number;
  ruleName: string;
  field: string;
  currentValue: string;
  suggestedValue: string;
  reason: string;
  confidence: number;
  status: "pending" | "approved" | "rejected" | "applied";
  resolvedAt: number | null;
  outcomeSampleCount: number;
  observationContext: string | null;
};

export type DeviceBaseline = {
  deviceId: string;
  metric: string;
  hour: number;
  avg: number;
  stdDev: number;
  sampleCount: number;
};

export async function fetchCortexStatus(signal?: AbortSignal): Promise<CortexStatus> {
  const r = await fetch("/api/cortex/status", { cache: "no-store", signal });
  return (await r.json()) as CortexStatus;
}

export async function fetchAdjustments(
  status?: string,
  signal?: AbortSignal,
): Promise<RuleSuggestion[]> {
  const url = status
    ? `/api/cortex/adjustments?status=${encodeURIComponent(status)}`
    : "/api/cortex/adjustments";
  const r = await fetch(url, { cache: "no-store", signal });
  const data = (await r.json()) as { ok: boolean; adjustments: RuleSuggestion[] };
  return Array.isArray(data.adjustments) ? data.adjustments : [];
}

export async function fetchDeviceBaselines(
  deviceId: string,
  signal?: AbortSignal,
): Promise<DeviceBaseline[]> {
  const r = await fetch(`/api/cortex/baselines/${encodeURIComponent(deviceId)}`, {
    cache: "no-store",
    signal,
  });
  const data = (await r.json()) as { ok: boolean; baselines: DeviceBaseline[] };
  return Array.isArray(data.baselines) ? data.baselines : [];
}

export async function runRuleAdvisor(): Promise<{ ok: boolean; count: number }> {
  const r = await fetch("/api/cortex/advisor/run", { method: "POST" });
  return (await r.json()) as { ok: boolean; count: number };
}

export async function resolveAdjustment(
  id: string,
  action: "approve" | "reject",
): Promise<boolean> {
  const r = await fetch(`/api/cortex/adjustments/${encodeURIComponent(id)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const data = (await r.json()) as { ok: boolean };
  return data.ok;
}

// ── Cortex Rules API (Phase 6: Nerve Center) ────────────────────────

export type CortexRule = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  condition: {
    sensor: string;
    operator: string;
    threshold: number;
    duration_seconds?: number;
    trend?: string | null;
    trend_window_minutes?: number;
    time_of_day?: { after: string; before: string } | null;
    forecast?: string | null;
    forecast_threshold?: number | null;
    forecast_within_minutes?: number;
    baseline_deviation?: number | null;
    scope?: string;
  };
  action: {
    target: string;
    action: string;
    value: boolean | number | string;
    reason: string;
    target_scope?: string;
  };
  source: "yaml" | "user" | "generated";
  modified: boolean;
  createdAt: number;
  updatedAt: number;
};

export async function fetchRules(signal?: AbortSignal): Promise<CortexRule[]> {
  const r = await fetch("/api/cortex/rules", { cache: "no-store", signal });
  const data = (await r.json()) as { ok: boolean; rules: CortexRule[] };
  return Array.isArray(data.rules) ? data.rules : [];
}

export async function toggleRule(id: string, enabled: boolean): Promise<boolean> {
  const r = await fetch(`/api/cortex/rules/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  const data = (await r.json()) as { ok: boolean };
  return data.ok;
}

export async function createRule(rule: {
  name: string;
  description: string;
  condition: CortexRule["condition"];
  action: CortexRule["action"];
  enabled?: boolean;
}): Promise<{ ok: boolean; rule?: CortexRule; error?: string }> {
  const r = await fetch("/api/cortex/rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rule),
  });
  return (await r.json()) as { ok: boolean; rule?: CortexRule; error?: string };
}

export async function updateRule(
  id: string,
  rule: {
    name: string;
    description: string;
    condition: CortexRule["condition"];
    action: CortexRule["action"];
    enabled?: boolean;
  },
): Promise<{ ok: boolean; rule?: CortexRule; error?: string }> {
  const r = await fetch(`/api/cortex/rules/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rule),
  });
  return (await r.json()) as { ok: boolean; rule?: CortexRule; error?: string };
}

export async function deleteRule(id: string): Promise<boolean> {
  const r = await fetch(`/api/cortex/rules/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  const data = (await r.json()) as { ok: boolean };
  return data.ok;
}

// Device capability helpers
export function hasSensor(device: Device, type: string): boolean {
  return device.capabilities.sensors.some((s) => s.type === type);
}

export function hasSensors(device: Device): boolean {
  return device.capabilities.sensors.length > 0;
}

export function hasActuators(device: Device): boolean {
  return device.capabilities.actuators.length > 0;
}

/** Best-effort unit resolution from a sensor ID prefix. */
export function guessSensorUnit(sensorId: string): string {
  if (sensorId.startsWith("temp")) return "\u00b0C";
  if (sensorId.startsWith("hum") || sensorId.startsWith("soil")) return "%";
  if (sensorId.startsWith("light")) return "lux";
  if (sensorId.startsWith("co2")) return "ppm";
  if (sensorId.startsWith("pressure")) return "hPa";
  return "";
}
