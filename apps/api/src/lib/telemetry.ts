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
  
  if (temp < -40 || temp > 80) {
    console.warn(`Invalid temperature reading: ${temp}°C (valid range: -40°C to 80°C)`);
    return null;
  }
  
  if (humidity < 0 || humidity > 100) {
    console.warn(`Invalid humidity reading: ${humidity}% (valid range: 0% to 100%)`);
    return null;
  }
  
  return { temp, humidity };
}
