import React, { useEffect, useRef, useState } from "react";
import Chart from "chart.js/auto";
import { fetchDeviceBaselines, type Device, type DeviceBaseline } from "../api";

type BaselinesProps = {
  devices: Device[];
  addError: (message: string, source?: string) => void;
};

export function NerveCenterBaselines({ devices, addError }: BaselinesProps): React.ReactElement {
  const [selectedDevice, setSelectedDevice] = useState<string>(devices[0]?.id ?? "");
  const [baselines, setBaselines] = useState<DeviceBaseline[]>([]);
  const [loading, setLoading] = useState(false);

  const tempChartRef = useRef<HTMLCanvasElement>(null);
  const humChartRef = useRef<HTMLCanvasElement>(null);
  const tempChartInstance = useRef<Chart | null>(null);
  const humChartInstance = useRef<Chart | null>(null);

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

    function renderChart(
      canvas: HTMLCanvasElement | null,
      existing: Chart | null,
      metric: string,
      label: string,
      unit: string,
      borderColor: string,
      bgColor: string,
    ): Chart | null {
      if (!canvas) return null;
      if (existing) existing.destroy();

      const { avgs, upperBounds, lowerBounds } = buildDataset(metric);

      return new Chart(canvas, {
        type: "line",
        data: {
          labels: hours,
          datasets: [
            {
              label: `Avg ${label}`,
              data: avgs,
              borderColor,
              backgroundColor: "transparent",
              borderWidth: 2,
              pointRadius: hours.map((_, i) => (i === currentHour ? 5 : 2)),
              pointBackgroundColor: hours.map((_, i) => (i === currentHour ? borderColor : "transparent")),
              pointBorderColor: hours.map((_, i) => (i === currentHour ? "#fff" : borderColor)),
              tension: 0.3,
              spanGaps: true,
            },
            {
              label: `+1\u03C3`,
              data: upperBounds,
              borderColor: "transparent",
              backgroundColor: bgColor,
              fill: "+1",
              pointRadius: 0,
              tension: 0.3,
              spanGaps: true,
            },
            {
              label: `-1\u03C3`,
              data: lowerBounds,
              borderColor: "transparent",
              backgroundColor: bgColor,
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
                    const { samples } = buildDataset(metric);
                    return `${ctx.parsed.y?.toFixed(1)}${unit} (${samples[ctx.dataIndex]} samples)`;
                  }
                  return `${ctx.parsed.y?.toFixed(1)}${unit}`;
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
                callback: (v) => `${v}${unit}`,
              },
            },
          },
        },
      });
    }

    tempChartInstance.current = renderChart(
      tempChartRef.current, tempChartInstance.current,
      "temperature", "Temperature", "\u00B0C",
      "rgb(239, 68, 68)", "rgba(239, 68, 68, 0.1)",
    );
    humChartInstance.current = renderChart(
      humChartRef.current, humChartInstance.current,
      "humidity", "Humidity", "%",
      "rgb(59, 130, 246)", "rgba(59, 130, 246, 0.1)",
    );

    return () => {
      tempChartInstance.current?.destroy();
      humChartInstance.current?.destroy();
      tempChartInstance.current = null;
      humChartInstance.current = null;
    };
  }, [baselines]);

  const tempBaselines = baselines.filter((b) => b.metric === "temperature");
  const humBaselines = baselines.filter((b) => b.metric === "humidity");

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
          {/* Temperature chart */}
          {tempBaselines.length > 0 && (
            <div className="rounded-xl border border-panel-border bg-panel/30 backdrop-blur-[6px] p-4">
              <div className="text-xs font-medium opacity-60 mb-3">Temperature Baselines (24h)</div>
              <div className="h-[200px]">
                <canvas ref={tempChartRef} />
              </div>
            </div>
          )}

          {/* Humidity chart */}
          {humBaselines.length > 0 && (
            <div className="rounded-xl border border-panel-border bg-panel/30 backdrop-blur-[6px] p-4">
              <div className="text-xs font-medium opacity-60 mb-3">Humidity Baselines (24h)</div>
              <div className="h-[200px]">
                <canvas ref={humChartRef} />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
