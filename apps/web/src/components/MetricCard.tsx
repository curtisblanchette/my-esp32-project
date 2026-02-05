import React, { useEffect, useMemo } from "react";
import { MetricChart } from "./MetricChart";
import { type LatestReading } from "../api";
import { useHistory, DateRangePreset, dateRangePresets } from "../hooks/useHistory";
import { formatDateForInput, fmt1 } from "../lib/format";

interface MetricCardProps {
  type: "temperature" | "humidity";
  currentValue: number | null;
  note: string;
  subtitle: string;
  mix?: number;
  latestReading?: LatestReading | null;
}

export function MetricCard(props: MetricCardProps): React.ReactElement {
  const isTemp = props.type === "temperature";

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
  } = useHistory();

  // Append real-time updates
  useEffect(() => {
    if (props.latestReading) {
      appendReading(props.latestReading);
    }
  }, [props.latestReading, appendReading]);

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
    <div className="glass-card rounded-2xl p-4 flex-1 min-w-0 [container-type:inline-size] overflow-hidden">
      <div className="flex items-baseline justify-between gap-3 mb-3">
        <div className="opacity-80 text-xs tracking-wider">{isTemp ? "Temperature" : "Relative Humidity"}</div>
        <div className="opacity-75 text-xs">{props.subtitle}</div>
      </div>

      <div
        className={`circle ${isTemp ? "tempCircle" : "humidityCircle"} rounded-full mx-auto relative grid place-items-center isolate mb-4`}
        style={isTemp ? { ["--t" as never]: props.mix ?? 0.5 } : { ["--h" as never]: props.currentValue ?? 0 }}
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
            <span className="readoutPill">{props.currentValue !== null ? fmt1(props.currentValue) : "--"}</span>
          </div>
          <div className="absolute top-0 right-2.5 text-sm font-bold opacity-90">{isTemp ? "°C" : "%"}</div>
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
              const roundedClass = isFirst ? "rounded-l-md" : isLast ? "rounded-r-md" : "rounded-none";

              return (
                <button
                  key={preset}
                  className={`px-3 py-1.5 border text-xs font-medium cursor-pointer transition-all duration-200 ${roundedClass} ${
                    dateRangePreset === preset
                      ? "bg-blue-500/20 border-blue-500/50 text-blue-500 font-semibold"
                      : "border-panel-border bg-gray-500/[0.08] hover:bg-gray-500/[0.15] hover:border-gray-500/40"
                  }`}
                  onClick={() => setDateRangePreset(preset)}
                >
                  {preset === "custom" ? "Custom" : preset.toUpperCase()}
                </button>
              );
            })}
          </div>
          {dateRangePreset === DateRangePreset.CUSTOM && (
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