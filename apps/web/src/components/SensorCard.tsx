import React, { useEffect, useMemo } from "react";
import { MetricChart } from "./MetricChart";
import { type LatestReading } from "../api";
import { useHistory, DateRangePreset, dateRangePresets } from "../hooks/useHistory";
import { formatDateForInput, fmt1 } from "../lib/format";

interface SensorCardProps {
  temp: number | null;
  humidity: number | null;
  tempNote: string;
  tempSubtitle: string;
  humidityNote: string;
  humiditySubtitle: string;
  mix?: number;
  latestReading?: LatestReading | null;
  deviceId?: string;
}

export function SensorCard(props: SensorCardProps): React.ReactElement {
  const {
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
  } = useHistory({ deviceId: props.deviceId });

  // Append real-time updates
  useEffect(() => {
    if (props.latestReading) {
      appendReading(props.latestReading);
    }
  }, [props.latestReading, appendReading]);

  const tempChartData = useMemo(() => {
    return history.map((p) => ({
      x: Number(p.ts),
      y: Number(p.temp),
    }));
  }, [history]);

  const humidityChartData = useMemo(() => {
    return history.map((p) => ({
      x: Number(p.ts),
      y: Number(p.humidity),
    }));
  }, [history]);

  return (
    <div className="glass-card rounded-2xl p-5 [container-type:inline-size] overflow-hidden min-w-0">
      {/* Gauges row */}
      <div className="flex justify-center gap-6 mb-6">
        {/* Temperature Gauge */}
        <div className="flex flex-col items-center flex-1">
          <div className="mb-3 w-full">
            <div className="opacity-80 text-xs tracking-wider text-left">Temperature</div>
          </div>
          <div
            className="circle tempCircle rounded-full relative grid place-items-center isolate"
            style={{ ["--t" as never]: props.mix ?? 0.5 }}
          >
            <div className="tempGlow" aria-hidden="true" />
            <div className="relative z-[2] text-center px-3">
              <div className="readoutBig font-extrabold leading-none tracking-tight">
                <span className="readoutPill">{props.temp !== null ? fmt1(props.temp) : "--"}</span>
              </div>
              <div className="absolute top-0 right-2.5 text-sm font-bold opacity-90">°C</div>
              <div className="mt-2.5 text-xs opacity-[0.78]">{props.tempNote}</div>
            </div>
          </div>
        </div>

        {/* Humidity Gauge */}
        <div className="flex flex-col items-center flex-1">
          <div className="mb-3 w-full">
            <div className="opacity-80 text-xs tracking-wider text-left">Relative Humidity</div>
          </div>
          <div
            className="circle humidityCircle rounded-full relative grid place-items-center isolate"
            style={{ ["--h" as never]: props.humidity ?? 0 }}
          >
            <div className="absolute rounded-full overflow-hidden z-[1] inset-[10px]" aria-hidden="true">
              <div className="waterFill" />
            </div>
            <div className="relative z-[2] text-center px-3">
              <div className="readoutBig font-extrabold leading-none tracking-tight">
                <span className="readoutPill">{props.humidity !== null ? fmt1(props.humidity) : "--"}</span>
              </div>
              <div className="absolute top-0 right-2.5 text-sm font-bold opacity-90">%</div>
              <div className="mt-2.5 text-xs opacity-[0.78]">{props.humidityNote}</div>
            </div>
          </div>
        </div>
      </div>

      {/* Shared Timeline Controls */}
      <div className="border-t border-panel-border pt-4">
        <div className="flex items-baseline justify-between gap-3 mb-3">
          <div className="opacity-80 text-xs tracking-wider">Timeline</div>
          <div className="opacity-75 text-xs">{historySub}</div>
        </div>

        <div className="flex justify-center mb-4">
          <div className="flex flex-wrap justify-center">
            {dateRangePresets.map((preset) => (
              <button
                key={preset}
                className={`px-3 py-1.5 border text-xs font-medium cursor-pointer transition-all duration-200 rounded-md ${
                  dateRangePreset === preset
                    ? "bg-blue-500/20 border-blue-500/50 text-blue-500 font-semibold"
                    : "border-panel-border bg-gray-500/[0.08] hover:bg-gray-500/[0.15] hover:border-gray-500/40"
                }`}
                onClick={() => setDateRangePreset(preset)}
              >
                {preset === "custom" ? "Custom" : preset.toUpperCase()}
              </button>
            ))}
          </div>
        </div>

        {dateRangePreset === DateRangePreset.CUSTOM && (
          <div className="flex justify-center gap-4 mb-4">
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

        {/* Charts side by side */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="relative w-full h-[clamp(140px,20vh,200px)]">
            <MetricChart
              data={tempChartData}
              timeRange={timeRangeBounds}
              label="Temp (°C)"
              color="rgba(251, 113, 133, 0.95)"
              backgroundColor="rgba(251, 113, 133, 0.20)"
              yMin={0}
              yMax={50}
              yTickFormat={(v: number) => `${v}°`}
            />
          </div>
          <div className="relative w-full h-[clamp(140px,20vh,200px)]">
            <MetricChart
              data={humidityChartData}
              timeRange={timeRangeBounds}
              label="Humidity (%)"
              color="rgba(56, 189, 248, 0.95)"
              backgroundColor="rgba(56, 189, 248, 0.20)"
              yMin={0}
              yMax={100}
              yTickFormat={(v: number) => `${v}%`}
              yAxisPosition="right"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
