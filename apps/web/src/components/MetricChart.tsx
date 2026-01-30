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
import { fmtTime, fmtTimeShort } from "../lib/format";

Chart.register(LineController, LineElement, PointElement, LinearScale, Tooltip, Legend, Filler);

export interface MetricChartProps {
  data: Array<{ x: number; y: number }>;
  timeRange: { sinceMs: number; untilMs: number };
  label: string;
  color: string;
  backgroundColor: string;
  yMin: number;
  yMax: number;
  yTickFormat: (value: number) => string;
}

export function MetricChart(props: MetricChartProps): React.ReactElement {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const chartRef = useRef<Chart | null>(null);

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
              label: props.label,
              data: props.data,
              borderColor: props.color,
              backgroundColor: props.backgroundColor,
              tension: 0.25,
              pointRadius: 0,
              borderWidth: 2,
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
            legend: { display: false },
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
                maxTicksLimit: 5,
                callback: (value) => fmtTimeShort(Number(value)),
              },
            },
            y: {
              type: "linear",
              min: props.yMin,
              max: props.yMax,
              grid: { color: "rgba(127, 127, 127, 0.18)" },
              ticks: { callback: (value) => props.yTickFormat(Number(value)) },
            },
          },
        },
      });

      return;
    }

    const chart = chartRef.current;
    chart.data.datasets[0]!.data = props.data as unknown as never[];

    if (chart.options.scales?.x) {
      chart.options.scales.x.min = axisRange.min;
      chart.options.scales.x.max = axisRange.max;
    }

    chart.update("none");
  }, [props.data, axisRange, props.label, props.color, props.backgroundColor, props.yMin, props.yMax, props.yTickFormat]);

  useEffect(() => {
    return () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    };
  }, []);

  return <canvas ref={canvasRef} className="block w-full h-full" />;
}