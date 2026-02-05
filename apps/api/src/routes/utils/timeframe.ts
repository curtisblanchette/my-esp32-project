// Helper to parse timeframe string (e.g., "24h", "7d") to milliseconds
export function parseTimeframe(timeframe: string): number {
  const match = timeframe.match(/^(\d+)(h|d)$/);
  if (!match) return 24 * 60 * 60 * 1000; // default 24h
  const [, num, unit] = match;
  const value = parseInt(num, 10);
  if (unit === "h") return value * 60 * 60 * 1000;
  if (unit === "d") return value * 24 * 60 * 60 * 1000;
  return 24 * 60 * 60 * 1000;
}