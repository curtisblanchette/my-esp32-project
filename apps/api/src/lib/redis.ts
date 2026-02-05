import { createClient } from "redis";
import { config } from "../config/index.js";

export type RedisReading = {
  ts: number;
  temp: number;
  humidity: number;
  sourceTopic: string | null;
  deviceId: string | null;
};

let redisClient: ReturnType<typeof createClient> | null = null;

export async function getRedisClient() {
  if (redisClient) return redisClient;

  redisClient = createClient({
    url: config.redisUrl,
  });

  redisClient.on("error", (err) => {
    console.error("Redis client error", err);
  });

  redisClient.on("connect", () => {
    console.log(`Redis connected: ${config.redisUrl}`);
  });

  await redisClient.connect();
  return redisClient;
}

export async function storeReading(reading: RedisReading): Promise<void> {
  const client = await getRedisClient();
  // Include deviceId in key for per-device storage (allows same timestamp from different devices)
  const key = reading.deviceId
    ? `reading:${reading.deviceId}:${reading.ts}`
    : `reading:${reading.ts}`;
  const ttl = 48 * 60 * 60;

  await client.setEx(key, ttl, JSON.stringify(reading));
}

export async function getReadingsInRange(sinceMs: number, untilMs: number, deviceId?: string): Promise<RedisReading[]> {
  const client = await getRedisClient();
  const readings: RedisReading[] = [];

  const keys: string[] = [];
  for await (const key of client.scanIterator({ MATCH: "reading:*", COUNT: 100 })) {
    keys.push(key);
  }

  if (keys.length === 0) return readings;

  const values = await client.mGet(keys);

  for (let i = 0; i < keys.length; i++) {
    const value = values[i];
    if (!value) continue;

    try {
      const reading = JSON.parse(value) as RedisReading;
      if (reading.ts >= sinceMs && reading.ts <= untilMs) {
        // Filter by deviceId if specified
        if (deviceId && reading.deviceId !== deviceId) continue;
        readings.push(reading);
      }
    } catch (e) {
      console.error(`Failed to parse reading from key ${keys[i]}`, e);
    }
  }

  readings.sort((a, b) => a.ts - b.ts);
  return readings;
}

export async function getAllReadings(): Promise<RedisReading[]> {
  const client = await getRedisClient();
  const readings: RedisReading[] = [];

  const keys: string[] = [];
  for await (const key of client.scanIterator({ MATCH: "reading:*", COUNT: 100 })) {
    keys.push(key);
  }

  if (keys.length === 0) return readings;

  const values = await client.mGet(keys);

  for (let i = 0; i < keys.length; i++) {
    const value = values[i];
    if (!value) continue;

    try {
      const reading = JSON.parse(value) as RedisReading;
      readings.push(reading);
    } catch (e) {
      console.error(`Failed to parse reading from key ${keys[i]}`, e);
    }
  }

  readings.sort((a, b) => a.ts - b.ts);
  return readings;
}

export async function deleteReadings(keys: string[]): Promise<void> {
  if (keys.length === 0) return;
  const client = await getRedisClient();
  await client.del(keys);
}
