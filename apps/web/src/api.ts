export type LatestReading = {
  temp: number;
  humidity: number;
  updatedAt: number;
  sourceTopic?: string;
  sourceIp?: string;
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
  signal?: AbortSignal;
}): Promise<HistoryPoint[]> {
  const limit = args.limit ?? 800;
  const bucketMs = args.bucketMs ?? 60_000;
  const url = `/api/history?sinceMs=${args.sinceMs}&untilMs=${args.untilMs}&limit=${limit}&bucketMs=${bucketMs}`;
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
