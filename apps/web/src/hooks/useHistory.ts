import { useState, useEffect, useCallback } from "react";
import { fetchHistory, type HistoryPoint, type LatestReading } from "../api";
import { fmtTimeShort } from "../lib/format";

export enum DateRangePreset {
  ONE_HOUR = "1h",
  SIX_HOUR = "6h",
  TWELVE_HOUR = "12h",
  TWENTY_FOUR_HOUR = "24h",
  SEVEN_DAY = "7d",
  THIRTY_DAY = "30d",
  CUSTOM = "custom",
}

export const dateRangePresets = Object.values(DateRangePreset);

function getPresetRange(
  preset: DateRangePreset,
  customStart?: number,
  customEnd?: number
): { sinceMs: number; untilMs: number } {
  const now = Date.now();
  if (preset === "custom" && customStart && customEnd) {
    return { sinceMs: customStart, untilMs: customEnd };
  }
  const ranges: Record<Exclude<DateRangePreset, "custom">, number> = {
    [DateRangePreset.ONE_HOUR]: 60 * 60 * 1000,
    [DateRangePreset.SIX_HOUR]: 6 * 60 * 60 * 1000,
    [DateRangePreset.TWELVE_HOUR]: 12 * 60 * 60 * 1000,
    [DateRangePreset.TWENTY_FOUR_HOUR]: 24 * 60 * 60 * 1000,
    [DateRangePreset.SEVEN_DAY]: 7 * 24 * 60 * 60 * 1000,
    [DateRangePreset.THIRTY_DAY]: 30 * 24 * 60 * 60 * 1000,
  };
  const duration = ranges[preset as Exclude<DateRangePreset, "custom">] || ranges["6h"];
  return { sinceMs: now - duration, untilMs: now };
}

function generateHistorySub(points: HistoryPoint[]): string {
  if (points.length === 0) {
    return "No history yet";
  }
  const firstTs = Number(points[0]!.ts);
  const lastTs = Number(points[points.length - 1]!.ts);
  return `${points.length} points | ${fmtTimeShort(firstTs)} → ${fmtTimeShort(lastTs)}`;
}

interface UseHistoryOptions {
  initialPreset?: DateRangePreset;
}

export function useHistory(options: UseHistoryOptions = {}) {
  const { initialPreset = DateRangePreset.ONE_HOUR } = options;

  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [historySub, setHistorySub] = useState<string>("--");
  const [dateRangePreset, setDateRangePreset] = useState<DateRangePreset>(initialPreset);
  const [customStartMs, setCustomStartMs] = useState<number>(Date.now() - 6 * 60 * 60 * 1000);
  const [customEndMs, setCustomEndMs] = useState<number>(Date.now());
  const [timeRangeBounds, setTimeRangeBounds] = useState<{ sinceMs: number; untilMs: number }>({
    sinceMs: Date.now() - 60 * 60 * 1000,
    untilMs: Date.now(),
  });

  // Fetch history when range changes
  useEffect(() => {
    const controller = new AbortController();

    async function loadHistory() {
      try {
        const { sinceMs, untilMs } = getPresetRange(dateRangePreset, customStartMs, customEndMs);
        setTimeRangeBounds({ sinceMs, untilMs });
        const points = await fetchHistory({
          sinceMs,
          untilMs,
          limit: 800,
          bucketMs: 60_000,
          signal: controller.signal,
        });
        setHistory(points);
        setHistorySub(generateHistorySub(points));
      } catch {
        setHistorySub("Error loading history");
      }
    }

    loadHistory();

    return () => {
      controller.abort();
    };
  }, [dateRangePreset, customStartMs, customEndMs]);

  // Append real-time updates
  const appendReading = useCallback(
    (reading: LatestReading) => {
      const newPoint: HistoryPoint = {
        ts: reading.updatedAt,
        temp: reading.temp,
        humidity: reading.humidity,
        count: 1,
      };

      setHistory((prev) => {
        // Only add if it's newer than the last point
        if (prev.length > 0) {
          const lastTs = Number(prev[prev.length - 1]!.ts);
          if (newPoint.ts <= lastTs) return prev;
        }

        // Add new point and keep only points within current time range
        const updated = [...prev, newPoint];
        const filtered = updated.filter(
          (p) =>
            Number(p.ts) >= timeRangeBounds.sinceMs &&
            Number(p.ts) <= timeRangeBounds.untilMs + 60000
        );

        setHistorySub(generateHistorySub(filtered));
        return filtered;
      });
    },
    [timeRangeBounds]
  );

  return {
    history,
    historySub,
    dateRangePreset,
    setDateRangePreset,
    customStartMs,
    setCustomStartMs,
    customEndMs,
    setCustomEndMs,
    timeRangeBounds,
    appendReading,
  };
}