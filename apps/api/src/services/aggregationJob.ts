import { getAllReadings, deleteReadings } from "../lib/redis.js";
import { getDb } from "../lib/sqlite.js";

const AGGREGATION_INTERVAL_MS = 10 * 60 * 1000;
const BUCKET_SIZE_MS = 5 * 60 * 1000;

type AggregatedBucket = {
  ts: number;
  tempSum: number;
  humiditySum: number;
  count: number;
  sourceTopic: string | null;
};

export function startAggregationJob(): void {
  console.log(`Starting aggregation job (interval: ${AGGREGATION_INTERVAL_MS / 1000}s, bucket: ${BUCKET_SIZE_MS / 1000}s)`);

  setInterval(() => {
    aggregateAndFlush().catch((err) => {
      console.error("Aggregation job failed", err);
    });
  }, AGGREGATION_INTERVAL_MS);

  setTimeout(() => {
    aggregateAndFlush().catch((err) => {
      console.error("Initial aggregation failed", err);
    });
  }, 5000);
}

async function aggregateAndFlush(): Promise<void> {
  const readings = await getAllReadings();

  if (readings.length === 0) {
    console.log("No readings to aggregate");
    return;
  }

  console.log(`Aggregating ${readings.length} readings from Redis`);

  const buckets = new Map<number, AggregatedBucket>();

  for (const reading of readings) {
    const bucketTs = Math.floor(reading.ts / BUCKET_SIZE_MS) * BUCKET_SIZE_MS;

    const existing = buckets.get(bucketTs);
    if (existing) {
      existing.tempSum += reading.temp;
      existing.humiditySum += reading.humidity;
      existing.count += 1;
    } else {
      buckets.set(bucketTs, {
        ts: bucketTs,
        tempSum: reading.temp,
        humiditySum: reading.humidity,
        count: 1,
        sourceTopic: reading.sourceTopic,
      });
    }
  }

  const db = getDb();
  const stmt = db.prepare(
    "INSERT INTO sensor_readings (ts, temp, humidity, source_topic) VALUES (?, ?, ?, ?)"
  );

  let insertedCount = 0;
  for (const bucket of buckets.values()) {
    const avgTemp = Math.round((bucket.tempSum / bucket.count) * 100) / 100;
    const avgHumidity = Math.round((bucket.humiditySum / bucket.count) * 100) / 100;

    stmt.run(bucket.ts, avgTemp, avgHumidity, bucket.sourceTopic);
    insertedCount++;
  }

  console.log(`Flushed ${insertedCount} aggregated buckets to SQLite (from ${readings.length} readings)`);

  const keysToDelete = readings.map((r) => `reading:${r.ts}`);
  await deleteReadings(keysToDelete);
  console.log(`Deleted ${keysToDelete.length} readings from Redis`);
}
