import React, { useEffect, useMemo } from "react";
import { MetricChart } from "./MetricChart";
import { type LatestReading, type Device, guessSensorUnit } from "../api";
import { useHistory, DateRangePreset, dateRangePresets } from "../hooks/useHistory";
import { formatDateForInput, fmt1, fmtTime } from '../lib/format';
import { GlassCard } from './ui-kit/GlassCard';

/** Sensor display config derived from device capabilities + latest reading */
type SensorDisplay = {
  id: string;
  type: string;
  label: string;
  unit: string;
  value: number | null;
  color: string;
  bgColor: string;
};

const SENSOR_COLORS: Record<string, { color: string; bg: string }> = {
  temperature: { color: "rgba(251, 113, 133, 0.95)", bg: "rgba(251, 113, 133, 0.20)" },
  humidity:    { color: "rgba(56, 189, 248, 0.95)",  bg: "rgba(56, 189, 248, 0.20)" },
  soil_moisture: { color: "rgba(52, 211, 153, 0.95)", bg: "rgba(52, 211, 153, 0.20)" },
  light_level: { color: "rgba(251, 191, 36, 0.95)",  bg: "rgba(251, 191, 36, 0.20)" },
  co2:         { color: "rgba(167, 139, 250, 0.95)", bg: "rgba(167, 139, 250, 0.20)" },
  pressure:    { color: "rgba(156, 163, 175, 0.95)", bg: "rgba(156, 163, 175, 0.20)" },
};
const DEFAULT_COLORS = { color: "rgba(148, 163, 184, 0.95)", bg: "rgba(148, 163, 184, 0.20)" };

function sensorLabel(type: string): string {
  const labels: Record<string, string> = {
    temperature: "Temperature",
    humidity: "Relative Humidity",
    soil_moisture: "Soil Moisture",
    light_level: "Light Level",
    co2: "CO\u2082",
    pressure: "Pressure",
  };
  return labels[type] || type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function guessSensorType(sensorId: string): string {
  if (sensorId.startsWith("temp")) return "temperature";
  if (sensorId.startsWith("hum")) return "humidity";
  if (sensorId.startsWith("soil")) return "soil_moisture";
  if (sensorId.startsWith("light")) return "light_level";
  if (sensorId.startsWith("co2")) return "co2";
  if (sensorId.startsWith("pressure")) return "pressure";
  if (sensorId.startsWith("contact")) return "contact";
  return sensorId;
}

interface SensorCardProps {
  device: Device;
  latestReading?: LatestReading | null;
}

export function SensorCard({ device, latestReading }: SensorCardProps): React.ReactElement {
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
  } = useHistory({ deviceId: device.id });

  // Append real-time updates
  useEffect(() => {
    if (latestReading) {
      appendReading(latestReading);
    }
  }, [latestReading, appendReading]);

  // Build sensor displays from device capabilities + latest reading
  const sensors: SensorDisplay[] = useMemo(() => {
    return device.capabilities.sensors.map((s) => {
      const stype = s.type || guessSensorType(s.id);
      const colors = SENSOR_COLORS[stype] || DEFAULT_COLORS;
      const value = latestReading?.readings?.[s.id] ?? null;
      return {
        id: s.id,
        type: stype,
        label: s.name || sensorLabel(stype),
        unit: s.unit || guessSensorUnit(s.id),
        value,
        color: colors.color,
        bgColor: colors.bg,
      };
    });
  }, [device.capabilities.sensors, latestReading]);

  // Separate temp and humidity for special gauge rendering
  const tempSensor = sensors.find((s) => s.type === "temperature");
  const humiditySensor = sensors.find((s) => s.type === "humidity");
  const otherSensors = sensors.filter((s) => s.type !== "temperature" && s.type !== "humidity");

  // Temperature gauge mix value for color gradient
  const mix = useMemo(() => {
    if (!tempSensor?.value) return 0.5;
    const t = tempSensor.value;
    const minC = 8, maxC = 30;
    const x = Math.max(0, Math.min(1, (t - minC) / (maxC - minC)));
    return Math.max(0, Math.min(1, 0.12 + Math.pow(x, 0.65) * 0.88));
  }, [tempSensor?.value]);

  return (
    <GlassCard variant={'col'}>
      {/* Gauges row — temp and humidity get special circle gauges */}
      {(tempSensor || humiditySensor) && (
        <div className="flex justify-center gap-6 mb-6">
          {tempSensor && (
            <div className="flex flex-col items-center flex-1">
              <div className="mb-3 w-full">
                <div className="opacity-80 text-xs tracking-wider text-center">{tempSensor.label}</div>
              </div>
              <div
                className="circle tempCircle rounded-full relative grid place-items-center isolate"
                style={{ ["--t" as never]: mix }}
              >
                <div className="tempGlow" aria-hidden="true" />
                <div className="relative z-[2] text-center px-3">
                  <div className="readoutBig font-extrabold leading-none tracking-tight">
                    <span className="readoutPill">{tempSensor.value !== null ? fmt1(tempSensor.value) : "--"}</span>
                  </div>
                  <div className="absolute top-0 right-2.5 text-sm font-bold opacity-90">{tempSensor.unit}</div>
                  <div className="mt-2.5 text-xs opacity-[0.78]">
                    {tempSensor.value !== null
                      ? tempSensor.value >= 25 ? "Warm" : tempSensor.value <= 18 ? "Cool" : "Comfortable"
                      : "Waiting..."}
                  </div>
                </div>
              </div>
            </div>
          )}

          {humiditySensor && (
            <div className="flex flex-col items-center flex-1">
              <div className="mb-3 w-full">
                <div className="opacity-80 text-xs tracking-wider text-center">{humiditySensor.label}</div>
              </div>
              <div
                className="circle humidityCircle rounded-full relative grid place-items-center isolate"
                style={{ ["--h" as never]: humiditySensor.value ?? 0 }}
              >
                <div className="absolute rounded-full overflow-hidden z-[1] inset-[10px]" aria-hidden="true">
                  <div className="waterFill" />
                </div>
                <div className="relative z-[2] text-center px-3">
                  <div className="readoutBig font-extrabold leading-none tracking-tight">
                    <span className="readoutPill">{humiditySensor.value !== null ? fmt1(humiditySensor.value) : "--"}</span>
                  </div>
                  <div className="absolute top-0 right-2.5 text-sm font-bold opacity-90">{humiditySensor.unit}</div>
                  <div className="mt-2.5 text-xs opacity-[0.78]">
                    {humiditySensor.value !== null
                      ? humiditySensor.value >= 70 ? "Air feels heavy" : humiditySensor.value <= 35 ? "Consider a humidifier" : "Nice range"
                      : "Waiting..."}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Generic sensor readouts for non-temp/humidity sensors */}
      {otherSensors.length > 0 && (
        <div className="grid grid-cols-2 gap-3 mb-4">
          {otherSensors.map((s) => (
            <div
              key={s.id}
              className="rounded-xl border border-panel-border bg-panel/20 backdrop-blur-[4px] p-3 text-center"
            >
              <div className="text-xs opacity-60 mb-1">{s.label}</div>
              <div className="text-2xl font-bold">
                {s.value !== null ? fmt1(s.value) : "--"}
                <span className="text-sm font-normal opacity-70 ml-1">{s.unit}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Shared Timeline Controls */}
      <div className="border-t border-panel-border pt-4 w-full">
        <div className="flex items-baseline justify-between gap-3 mb-3">
          <div className="opacity-80 text-xs tracking-wider">Timeline</div>
          <div className="opacity-75 text-xs">{historySub}</div>
        </div>

        <div className="mb-4">
          <div className="flex">
            {dateRangePresets.map((preset, index) => {
              const isFirst = index === 0;
              const isLast = index === dateRangePresets.length - 1;
              const roundedClass = isFirst ? "rounded-l-md" : isLast ? "rounded-r-md" : "rounded-none";

              return (<button
                key={ preset }
                className={ `flex-1 px-3 py-1.5 border text-xs font-medium cursor-pointer transition-all duration-200 ${roundedClass} ${
                  dateRangePreset === preset
                    ? "bg-blue-500/20 border-blue-500/50 text-blue-500 font-semibold"
                    : "border-panel-border bg-gray-500/[0.08] hover:bg-gray-500/[0.15] hover:border-gray-500/40"
                }` }
                onClick={ () => setDateRangePreset(preset) }
              >
                { preset === "custom" ? "Custom" : preset.toUpperCase() }
              </button>);
            })}
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

        {/* Charts — one per sensor */}
        <div className={`grid gap-1 ${sensors.length <= 2 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3"}`}>
          {sensors.map((s) => {
            const chartData = history
              .filter((p) => p.readings?.[s.id] != null)
              .map((p) => ({ x: Number(p.ts), y: Number(p.readings[s.id]) }));

            const yBounds = s.type === "temperature" ? { yMin: 0, yMax: 50 }
              : s.type === "humidity" || s.type === "soil_moisture" ? { yMin: 0, yMax: 100 }
              : {};

            return (
              <div key={s.id} className="relative w-full h-[clamp(140px,20vh,200px)]">
                <MetricChart
                  data={chartData}
                  timeRange={timeRangeBounds}
                  label={`${s.label} (${s.unit})`}
                  color={s.color}
                  backgroundColor={s.bgColor}
                  yTickFormat={(v: number) => `${v}${s.unit}`}
                  {...yBounds}
                />
              </div>
            );
          })}
        </div>
      </div>
      <div className="mt-2 text-xs flex align-right justify-end opacity-75">
        {latestReading
          ? `Last update: ${fmtTime(latestReading.updatedAt)}`
          : `Waiting for first reading...`}
      </div>
    </GlassCard>
  );
}
