import React, { useState, useEffect, useMemo } from "react";
import { MetricChart } from "./MetricChart";
import { fetchHistory, type HistoryPoint, type LatestReading } from "../api";

enum DateRangePreset {
  ONE_HOUR = '1h',
  SIX_HOUR = '6h',
  TWELVE_HOUR = '12h',
  TWENTY_FOUR_HOUR = '24h',
  SEVEN_DAY = '7d',
  THIRTY_DAY = '30d',
  CUSTOM = 'custom'
}
const dateRangePresets = Object.values(DateRangePreset);

function getPresetRange(preset: DateRangePreset, customStart?: number, customEnd?: number): {
  sinceMs: number;
  untilMs: number
} {
  const now = Date.now();
  if (preset === 'custom' && customStart && customEnd) {
    return { sinceMs: customStart, untilMs: customEnd };
  }
  const ranges: Record<Exclude<DateRangePreset, 'custom'>, number> = {
    [DateRangePreset.ONE_HOUR]: 60 * 60 * 1000,
    [DateRangePreset.SIX_HOUR]: 6 * 60 * 60 * 1000,
    [DateRangePreset.TWELVE_HOUR]: 12 * 60 * 60 * 1000,
    [DateRangePreset.TWENTY_FOUR_HOUR]: 24 * 60 * 60 * 1000,
    [DateRangePreset.SEVEN_DAY]: 7 * 24 * 60 * 60 * 1000,
    [DateRangePreset.THIRTY_DAY]: 30 * 24 * 60 * 60 * 1000,
  };
  const duration = ranges[preset as Exclude<DateRangePreset, 'custom'>] || ranges['6h'];
  return { sinceMs: now - duration, untilMs: now };
}

function formatDateForInput(ms: number): string {
  const d = new Date(ms);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hours = String(d.getHours()).padStart(2, '0');
  const minutes = String(d.getMinutes()).padStart(2, '0');
  return `${ year }-${ month }-${ day }T${ hours }:${ minutes }`;
}

function fmtTimeShort(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

interface MetricCardProps {
  type: "temperature" | "humidity";
  currentValue: number | null;
  note: string;
  subtitle: string;
  mix?: number;
  latestReading?: LatestReading | null;
}

function fmt1(n: number): string {
  return Number.isFinite(n) ? n.toFixed(1) : '--';
}

export function MetricCard(props: MetricCardProps): React.ReactElement {
  const isTemp = props.type === "temperature";
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [historySub, setHistorySub] = useState<string>('--');
  const [dateRangePreset, setDateRangePreset] = useState<DateRangePreset>(DateRangePreset.ONE_HOUR);
  const [customStartMs, setCustomStartMs] = useState<number>(Date.now() - 6 * 60 * 60 * 1000);
  const [customEndMs, setCustomEndMs] = useState<number>(Date.now());
  const [timeRangeBounds, setTimeRangeBounds] = useState<{
    sinceMs: number;
    untilMs: number
  }>({ sinceMs: Date.now() - 60 * 60 * 1000, untilMs: Date.now() });

  // Fetch initial history data when range changes
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
          signal: controller.signal
        });
        setHistory(points);

        if (points.length === 0) {
          setHistorySub('No history yet');
          return;
        }

        const firstTs = Number(points[0]!.ts);
        const lastTs = Number(points[points.length - 1]!.ts);
        setHistorySub(`${ points.length } points | ${ fmtTimeShort(firstTs) } → ${ fmtTimeShort(lastTs) }`);
      } catch {
        setHistorySub('Error loading history');
      }
    }

    loadHistory();

    return () => {
      controller.abort();
    };
  }, [dateRangePreset, customStartMs, customEndMs]);

  // Real-time updates: append new readings to history
  useEffect(() => {
    if (!props.latestReading) return;

    const newPoint: HistoryPoint = {
      ts: props.latestReading.updatedAt,
      temp: props.latestReading.temp,
      humidity: props.latestReading.humidity,
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
      const filtered = updated.filter((p) => 
        Number(p.ts) >= timeRangeBounds.sinceMs && Number(p.ts) <= timeRangeBounds.untilMs + 60000
      );

      // Update subtitle
      if (filtered.length > 0) {
        const firstTs = Number(filtered[0]!.ts);
        const lastTs = Number(filtered[filtered.length - 1]!.ts);
        setHistorySub(`${ filtered.length } points | ${ fmtTimeShort(firstTs) } → ${ fmtTimeShort(lastTs) }`);
      }

      return filtered;
    });
  }, [props.latestReading, timeRangeBounds]);
  
  const chartData = useMemo(() => {
    return history.map((p) => ({
      x: Number(p.ts),
      y: Number(isTemp ? p.temp : p.humidity),
    }));
  }, [history, isTemp]);

  const chartConfig = isTemp
    ? {
        label: "Temp (°C)",
        color: "rgba(251, 113, 133, 0.95)",
        backgroundColor: "rgba(251, 113, 133, 0.20)",
        yMin: 0,
        yMax: 50,
        yTickFormat: (v: number) => `${v}°`,
      }
    : {
        label: "Humidity (%)",
        color: "rgba(56, 189, 248, 0.95)",
        backgroundColor: "rgba(56, 189, 248, 0.20)",
        yMin: 0,
        yMax: 100,
        yTickFormat: (v: number) => `${v}%`,
      };

  return (
    <div className="glass-card rounded-2xl p-4 flex-1 min-w-[min(420px,100%)] [container-type:inline-size]">
      <div className="flex items-baseline justify-between gap-3 mb-3">
        <div className="opacity-80 text-xs tracking-wider">
          {isTemp ? "Temperature" : "Relative Humidity"}
        </div>
        <div className="opacity-75 text-xs">{props.subtitle}</div>
      </div>

      <div
        className={`circle ${isTemp ? "tempCircle" : "humidityCircle"} rounded-full mx-auto relative grid place-items-center isolate mb-4`}
        style={isTemp ? { ['--t' as never]: props.mix ?? 0.5 } : { ['--h' as never]: props.currentValue ?? 0 }}
      >
        {isTemp ? (
          <div className="tempGlow" aria-hidden="true" />
        ) : (
          <div className="absolute rounded-full overflow-hidden z-[1] inset-[10px]" aria-hidden="true">
            <div className="waterFill" />
          </div>
        )}
        <div className="relative z-[2] text-center px-3">
          <div className="readoutBig font-extrabold leading-none tracking-tight">
            <span className="readoutPill">
              {props.currentValue !== null ? fmt1(props.currentValue) : '--'}
            </span>
          </div>
          <div className="absolute top-0 right-2.5 text-sm font-bold opacity-90">
            {isTemp ? "°C" : "%"}
          </div>
          <div className="mt-2.5 text-xs opacity-[0.78]">{props.note}</div>
        </div>
      </div>

      <div className="mt-4">
        <div className="flex items-baseline justify-between gap-3 mb-2">
          <div className="opacity-80 text-xs tracking-wider">Timeline</div>
          <div className="opacity-75 text-xs">{historySub}</div>
        </div>
        
        <div className="mb-3">
          <div className="flex mb-2 justify-end flex-wrap">
            {dateRangePresets.map((preset, index) => {
              const isFirst = index === 0;
              const isLast = index === dateRangePresets.length - 1;
              const roundedClass = isFirst ? 'rounded-l-md' : isLast ? 'rounded-r-md' : 'rounded-none';
              
              return (
                <button
                  key={preset}
                  className={`px-3 py-1.5 border text-xs font-medium cursor-pointer transition-all duration-200 ${roundedClass} ${
                    dateRangePreset === preset
                      ? 'bg-blue-500/20 border-blue-500/50 text-blue-500 font-semibold'
                      : 'border-panel-border bg-gray-500/[0.08] hover:bg-gray-500/[0.15] hover:border-gray-500/40'
                  }`}
                  onClick={() => setDateRangePreset(preset)}
                >
                  {preset === 'custom' ? 'Custom' : preset.toUpperCase()}
                </button>
              );
            })}
          </div>
          {dateRangePreset === 'custom' && (
            <div className="flex flex-col gap-2 p-2 rounded-lg bg-gray-500/[0.08]">
              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium opacity-80">From:</span>
                <input
                  type="datetime-local"
                  className="px-2 py-1.5 border border-panel-border rounded-md bg-white/5 text-inherit text-xs font-[inherit] focus:outline-none focus:border-blue-500/50 focus:bg-white/[0.08]"
                  value={formatDateForInput(customStartMs)}
                  onChange={(e) => setCustomStartMs(new Date(e.target.value).getTime())}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium opacity-80">To:</span>
                <input
                  type="datetime-local"
                  className="px-2 py-1.5 border border-panel-border rounded-md bg-white/5 text-inherit text-xs font-[inherit] focus:outline-none focus:border-blue-500/50 focus:bg-white/[0.08]"
                  value={formatDateForInput(customEndMs)}
                  onChange={(e) => setCustomEndMs(new Date(e.target.value).getTime())}
                />
              </label>
            </div>
          )}
        </div>

        <div className="relative w-full h-[clamp(160px,25vh,240px)]">
          <MetricChart
            data={chartData}
            timeRange={timeRangeBounds}
            label={chartConfig.label}
            color={chartConfig.color}
            backgroundColor={chartConfig.backgroundColor}
            yMin={chartConfig.yMin}
            yMax={chartConfig.yMax}
            yTickFormat={chartConfig.yTickFormat}
          />
        </div>
      </div>
    </div>
  );
}
