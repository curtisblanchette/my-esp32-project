import type { SensorReading } from "../types/sensor.js";

let latest: SensorReading | null = null;

export function setLatest(next: SensorReading): void {
  latest = next;
}

export function getLatest(): SensorReading | null {
  return latest;
}
