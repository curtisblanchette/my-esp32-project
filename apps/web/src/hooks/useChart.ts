import { useEffect, useRef } from "react";
import {
  Chart,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  Tooltip,
  Legend,
  Filler,
  type ChartConfiguration,
} from "chart.js";

Chart.register(LineController, LineElement, PointElement, LinearScale, Tooltip, Legend, Filler);

interface UseChartOptions {
  config: Omit<ChartConfiguration<"line">, "type">;
  onUpdate?: (chart: Chart) => void;
}

export function useChart(options: UseChartOptions) {
  const { config, onUpdate } = options;
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const chartRef = useRef<Chart | null>(null);

  useEffect(() => {
    if (!canvasRef.current) return;

    if (!chartRef.current) {
      const ctx = canvasRef.current.getContext("2d");
      if (!ctx) return;

      chartRef.current = new Chart(ctx, {
        type: "line",
        ...config,
      });
      return;
    }

    // Update existing chart
    if (onUpdate) {
      onUpdate(chartRef.current);
    }
    chartRef.current.update("none");
  }, [config, onUpdate]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    };
  }, []);

  return { canvasRef, chartRef };
}