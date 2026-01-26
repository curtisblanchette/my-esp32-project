export function parseNumber(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return n;
}

export function parseTelemetry(payload: unknown): { temp: number; humidity: number } | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;

  const tempRaw = p.temp ?? p.tempC ?? p.temperature ?? p.temperatureC;
  const humidityRaw = p.humidity ?? p.hum;

  const temp = typeof tempRaw === "number" ? tempRaw : typeof tempRaw === "string" ? Number(tempRaw) : NaN;
  const humidity =
    typeof humidityRaw === "number" ? humidityRaw : typeof humidityRaw === "string" ? Number(humidityRaw) : NaN;

  if (!Number.isFinite(temp) || !Number.isFinite(humidity)) return null;
  return { temp, humidity };
}
