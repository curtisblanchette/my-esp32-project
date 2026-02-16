import React, { useEffect, useRef, useState } from "react";
import Chart from "chart.js/auto";
import { fetchDeviceBaselines, guessSensorUnit, type Device, type DeviceBaseline } from "../api";

type BaselinesProps = {
  devices: Device[];
  addError: (message: string, source?: string) => void;
};

const METRIC_COLORS: Record<string, { border: string; bg: string }> = {
  temperature:   { border: "rgb(239, 68, 68)",   bg: "rgba(239, 68, 68, 0.1)" },
  humidity:      { border: "rgb(59, 130, 246)",   bg: "rgba(59, 130, 246, 0.1)" },
  soil_moisture: { border: "rgb(52, 211, 153)",   bg: "rgba(52, 211, 153, 0.1)" },
  light_level:   { border: "rgb(251, 191, 36)",   bg: "rgba(251, 191, 36, 0.1)" },
  co2:           { border: "rgb(167, 139, 250)",  bg: "rgba(167, 139, 250, 0.1)" },
  pressure:      { border: "rgb(156, 163, 175)",  bg: "rgba(156, 163, 175, 0.1)" },
};
const DEFAULT_METRIC_COLOR = { border: "rgb(148, 163, 184)", bg: "rgba(148, 163, 184, 0.1)" };

function metricLabel(metric: string): string {
  const labels: Record<string, string> = {
    temperature: "Temperature",
    humidity: "Humidity",
    soil_moisture: "Soil Moisture",
    light_level: "Light Level",
    co2: "CO\u2082",
    pressure: "Pressure",
  };
  return labels[metric] || metric.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function NerveCenterBaselines({ devices, addError }: BaselinesProps): React.ReactElement {
  const [selectedDevice, setSelectedDevice] = useState<string>(devices[0]?.id ?? "");
  const [baselines, setBaselines] = useState<DeviceBaseline[]>([]);
  const [loading, setLoading] = useState(false);

  const chartRefs = useRef<Map<string, HTMLCanvasElement>>(new Map());
  const chartInstances = useRef<Map<string, Chart>>(new Map());

  // Sync selectedDevice when devices arrive after mount
  useEffect(() => {
    if (!selectedDevice && devices.length > 0) {
      setSelectedDevice(devices[0].id);
    }
  }, [devices, selectedDevice]);

  // Fetch baselines when device changes
  useEffect(() => {
    if (!selectedDevice) return;
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      try {
        const data = await fetchDeviceBaselines(selectedDevice, controller.signal);
        setBaselines(data);
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        addError("Failed to fetch baselines", "Cortex");
      } finally {
        setLoading(false);
      }
    }

    load();
    return () => controller.abort();
  }, [selectedDevice, addError]);

  // Discover unique metrics from baselines
  const uniqueMetrics = [...new Set(baselines.map((b) => b.metric))].sort();

  // Render charts when baselines change
  useEffect(() => {
    if (!baselines.length) return;

    const hours = Array.from({ length: 24 }, (_, i) => `${i}:00`);
    const currentHour = new Date().getHours();

    function buildDataset(metric: string) {
      const byHour = new Map<number, DeviceBaseline>();
      for (const b of baselines) {
        if (b.metric === metric) byHour.set(b.hour, b);
      }

      const avgs = hours.map((_, i) => byHour.get(i)?.avg ?? null);
      const upperBounds = hours.map((_, i) => {
        const b = byHour.get(i);
        return b ? b.avg + b.stdDev : null;
      });
      const lowerBounds = hours.map((_, i) => {
        const b = byHour.get(i);
        return b ? b.avg - b.stdDev : null;
      });
      const samples = hours.map((_, i) => byHour.get(i)?.sampleCount ?? 0);

      return { avgs, upperBounds, lowerBounds, samples };
    }

    // Destroy old charts
    for (const [, chart] of chartInstances.current) {
      chart.destroy();
    }
    chartInstances.current.clear();

    for (const metric of uniqueMetrics) {
      const canvas = chartRefs.current.get(metric);
      if (!canvas) continue;

      const colors = METRIC_COLORS[metric] || DEFAULT_METRIC_COLOR;
      const unit = guessSensorUnit(metric.replace(/_/g, "").slice(0, 4));
      // Better unit resolution: use the metric name directly
      const displayUnit = (() => {
        if (metric === "temperature") return "\u00B0C";
        if (metric === "humidity" || metric === "soil_moisture") return "%";
        if (metric === "light_level") return "lux";
        if (metric === "co2") return "ppm";
        if (metric === "pressure") return "hPa";
        return unit;
      })();

      const { avgs, upperBounds, lowerBounds, samples } = buildDataset(metric);

      const chart = new Chart(canvas, {
        type: "line",
        data: {
          labels: hours,
          datasets: [
            {
              label: `Avg ${metricLabel(metric)}`,
              data: avgs,
              borderColor: colors.border,
              backgroundColor: "transparent",
              borderWidth: 2,
              pointRadius: hours.map((_, i) => (i === currentHour ? 5 : 2)),
              pointBackgroundColor: hours.map((_, i) => (i === currentHour ? colors.border : "transparent")),
              pointBorderColor: hours.map((_, i) => (i === currentHour ? "#fff" : colors.border)),
              tension: 0.3,
              spanGaps: true,
            },
            {
              label: `+1\u03C3`,
              data: upperBounds,
              borderColor: "transparent",
              backgroundColor: colors.bg,
              fill: "+1",
              pointRadius: 0,
              tension: 0.3,
              spanGaps: true,
            },
            {
              label: `-1\u03C3`,
              data: lowerBounds,
              borderColor: "transparent",
              backgroundColor: colors.bg,
              fill: "-1",
              pointRadius: 0,
              tension: 0.3,
              spanGaps: true,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (ctx) => {
                  if (ctx.datasetIndex === 0) {
                    return `${ctx.parsed.y?.toFixed(1)}${displayUnit} (${samples[ctx.dataIndex]} samples)`;
                  }
                  return `${ctx.parsed.y?.toFixed(1)}${displayUnit}`;
                },
              },
            },
          },
          scales: {
            x: {
              grid: { color: "rgba(255,255,255,0.05)" },
              ticks: { color: "rgba(255,255,255,0.3)", font: { size: 10 } },
            },
            y: {
              grid: { color: "rgba(255,255,255,0.05)" },
              ticks: {
                color: "rgba(255,255,255,0.3)",
                font: { size: 10 },
                callback: (v) => `${v}${displayUnit}`,
              },
            },
          },
        },
      });

      chartInstances.current.set(metric, chart);
    }

    return () => {
      for (const [, chart] of chartInstances.current) {
        chart.destroy();
      }
      chartInstances.current.clear();
    };
  }, [baselines, uniqueMetrics.join(",")]);

  return (
    <div className="space-y-4">
      {/* Device selector */}
      <div className="flex items-center gap-3">
        <label className="text-xs opacity-50">Device:</label>
        <select
          value={selectedDevice}
          onChange={(e) => setSelectedDevice(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg border border-panel-border bg-panel/30 text-white cursor-pointer"
        >
          {devices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name || d.id}
            </option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="text-sm opacity-50 text-center py-8">Loading baselines...</div>
      ) : baselines.length === 0 ? (
        <div className="text-sm opacity-50 text-center py-8">
          No baselines learned yet for this device. Baselines build over time from sensor readings.
        </div>
      ) : (
        <>
          {uniqueMetrics.map((metric) => (
            <div key={metric} className="rounded-xl border border-panel-border bg-panel/30 backdrop-blur-[6px] p-4">
              <div className="text-xs font-medium opacity-60 mb-3">{metricLabel(metric)} Baselines (24h)</div>
              <div className="h-[200px]">
                <canvas
                  ref={(el) => {
                    if (el) chartRefs.current.set(metric, el);
                    else chartRefs.current.delete(metric);
                  }}
                />
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
