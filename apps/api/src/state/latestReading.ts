import type { SensorReading } from "../types/sensor.js";

// Per-device latest readings
const latestByDevice = new Map<string, SensorReading>();

// Legacy single latest reading (for backward compatibility)
let latest: SensorReading | null = null;

export function setLatest(next: SensorReading): void {
  latest = next;
  if (next.deviceId) {
    latestByDevice.set(next.deviceId, next);
  }
}

export function getLatest(): SensorReading | null {
  return latest;
}

export function getLatestByDevice(deviceId: string): SensorReading | null {
  return latestByDevice.get(deviceId) ?? null;
}

export function getAllLatestByDevice(): Record<string, SensorReading> {
  return Object.fromEntries(latestByDevice);
}
