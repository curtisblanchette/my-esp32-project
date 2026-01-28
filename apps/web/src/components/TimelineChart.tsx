import React, { useEffect, useMemo, useRef } from "react";
import {
  Chart,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  Tooltip,
  Legend,
  Filler,
} from "chart.js";

import type { HistoryPoint } from "../api";

Chart.register(LineController, LineElement, PointElement, LinearScale, Tooltip, Legend, Filler);

function fmtTime(ms: number): string {
  return new Date(ms).toLocaleString();
}

function fmtTimeShort(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function TimelineChart(props: { points: HistoryPoint[]; timeRange: { sinceMs: number; untilMs: number } }): React.ReactElement {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const chartRef = useRef<Chart | null>(null);

  const series = useMemo(() => {
    const temp = props.points.map((p) => ({ x: Number(p.ts), y: Number(p.temp) }));
    const humidity = props.points.map((p) => ({ x: Number(p.ts), y: Number(p.humidity) }));
    return { temp, humidity };
  }, [props.points]);

  const axisRange = useMemo(() => {
    const duration = props.timeRange.untilMs - props.timeRange.sinceMs;
    const padding = Math.max(60_000, duration * 0.01);
    return {
      min: props.timeRange.sinceMs - padding,
      max: props.timeRange.untilMs + padding,
    };
  }, [props.timeRange]);

  useEffect(() => {
    if (!canvasRef.current) return;

    if (!chartRef.current) {
      const ctx = canvasRef.current.getContext("2d");
      if (!ctx) return;

      chartRef.current = new Chart(ctx, {
        type: "line",
        data: {
          datasets: [
            {
              label: "Temp (°C)",
              data: series.temp,
              borderColor: "rgba(244, 63, 94, 0.95)",
              backgroundColor: "rgba(244, 63, 94, 0.20)",
              tension: 0.25,
              pointRadius: 0,
              borderWidth: 2,
              yAxisID: "yTemp",
              fill: true,
            },
            {
              label: "Humidity (%)",
              data: series.humidity,
              borderColor: "rgba(59, 130, 246, 0.95)",
              backgroundColor: "rgba(59, 130, 246, 0.20)",
              tension: 0.25,
              pointRadius: 0,
              borderWidth: 2,
              yAxisID: "yHumidity",
              fill: true,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          normalized: true,
          parsing: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: true },
            tooltip: {
              callbacks: {
                title: (items) => {
                  if (!items || items.length === 0) return "";
                  const x = (items[0] as { parsed: { x: number } }).parsed.x;
                  return fmtTime(x);
                },
              },
            },
          },
          scales: {
            x: {
              type: "linear",
              min: axisRange.min,
              max: axisRange.max,
              grid: { color: "rgba(127, 127, 127, 0.18)" },
              ticks: {
                maxTicksLimit: 7,
                callback: (value) => fmtTimeShort(Number(value)),
              },
            },
            yTemp: {
              type: "linear",
              position: "left",
              grid: { color: "rgba(127, 127, 127, 0.18)" },
              ticks: { callback: (v) => `${v}°` },
            },
            yHumidity: {
              type: "linear",
              position: "right",
              min: 0,
              max: 100,
              grid: { drawOnChartArea: false },
              ticks: { callback: (v) => `${v}%` },
            },
          },
        },
      });

      return;
    }

    const chart = chartRef.current;
    chart.data.datasets[0]!.data = series.temp as unknown as never[];
    chart.data.datasets[1]!.data = series.humidity as unknown as never[];
    
    if (chart.options.scales?.x) {
      chart.options.scales.x.min = axisRange.min;
      chart.options.scales.x.max = axisRange.max;
    }
    
    chart.update("none");
  }, [series, axisRange]);

  useEffect(() => {
    return () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    };
  }, []);

  return <canvas ref={canvasRef} className="block w-full h-full" />;
}
