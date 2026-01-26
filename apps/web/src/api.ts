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
