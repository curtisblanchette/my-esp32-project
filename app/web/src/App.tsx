import React, { useEffect, useMemo, useState } from "react";

import { fetchHistory, fetchLatest, type HistoryPoint, type LatestReading } from "./api";
import { TimelineChart } from "./components/TimelineChart";

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

function fmt1(n: number): string {
  return Number.isFinite(n) ? n.toFixed(1) : "--";
}

function tempToMix(tC: number): number {
  const minC = 8;
  const maxC = 30;
  const x = clamp((tC - minC) / (maxC - minC), 0, 1);
  const eased = Math.pow(x, 0.65);
  return clamp(0.12 + eased * 0.88, 0, 1);
}

function fmtTime(ms: number): string {
  return new Date(ms).toLocaleString();
}

function fmtTimeShort(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function App(): React.ReactElement {
  const [latest, setLatest] = useState<LatestReading | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [historySub, setHistorySub] = useState<string>("--");

  useEffect(() => {
    const controller = new AbortController();

    async function refresh() {
      try {
        const l = await fetchLatest(controller.signal);
        setLatest(l);
      } catch {
        // ignore
      }
    }

    refresh();
    const t = window.setInterval(refresh, 2000);

    return () => {
      controller.abort();
      window.clearInterval(t);
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    async function refresh() {
      try {
        const now = Date.now();
        const sinceMs = now - 6 * 60 * 60 * 1000;
        const points = await fetchHistory({ sinceMs, untilMs: now, limit: 800, bucketMs: 60_000, signal: controller.signal });
        setHistory(points);

        if (points.length === 0) {
          setHistorySub("No history yet");
          return;
        }

        const firstTs = Number(points[0]!.ts);
        const lastTs = Number(points[points.length - 1]!.ts);
        setHistorySub(`${points.length} points | ${fmtTimeShort(firstTs)} → ${fmtTimeShort(lastTs)}`);
      } catch {
        setHistorySub("Error loading history");
      }
    }

    refresh();
    const t = window.setInterval(refresh, 10000);

    return () => {
      controller.abort();
      window.clearInterval(t);
    };
  }, []);

  const derived = useMemo(() => {
    if (!latest) return null;
    const t = Number(latest.temp);
    const h = clamp(Number(latest.humidity), 0, 100);
    const mix = tempToMix(t);

    const humiditySub = h >= 70 ? "Humid" : h <= 35 ? "Dry" : "Comfortable";
    const tempNote = t >= 25 ? "Warm" : t <= 18 ? "Cool" : "Comfortable";
    const humidityNote = h >= 70 ? "Air feels heavy" : h <= 35 ? "Consider a humidifier" : "Nice range";

    return { t, h, mix, humiditySub, tempNote, humidityNote };
  }, [latest]);

  return (
    <div className="card">
      <h1>Sensor Dashboard</h1>

      <div className="grid">
        <div className="panel gaugePanel">
          <div className="gaugeHeader">
            <div className="gaugeLabel">Temperature</div>
            <div className="small">Cool → Warm</div>
          </div>

          <div className="circle tempCircle" style={{ ["--t" as never]: derived?.mix ?? 0.5 }}>
            <div className="tempGlow" aria-hidden="true" />
            <div className="readout">
              <div className="readoutBig">
                <span className="readoutPill">{derived ? fmt1(derived.t) : "--"}</span>
              </div>
              <div className="readoutUnit">°C</div>
              <div className="note">{derived ? derived.tempNote : "Waiting…"}</div>
            </div>
          </div>
        </div>

        <div className="panel gaugePanel">
          <div className="gaugeHeader">
            <div className="gaugeLabel">Relative Humidity</div>
            <div className="small">{derived ? derived.humiditySub : "--"}</div>
          </div>

          <div className="circle humidityCircle" style={{ ["--h" as never]: derived?.h ?? 0 }}>
            <div className="water" aria-hidden="true">
              <div className="waterFill" />
            </div>
            <div className="readout">
              <div className="readoutBig">
                <span className="readoutPill">{derived ? fmt1(derived.h) : "--"}</span>
              </div>
              <div className="readoutUnit">%</div>
              <div className="note">{derived ? derived.humidityNote : "Waiting…"}</div>
            </div>
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 12 }}>
        <div className="gaugeHeader">
          <div className="gaugeLabel">Timeline</div>
          <div className="small">{historySub}</div>
        </div>

        <div className="chartCanvas">
          <TimelineChart points={history} />
        </div>
      </div>

      <div className="meta">
        {latest ? `Last update: ${fmtTime(latest.updatedAt)}` : "Waiting for first reading..."}
      </div>
    </div>
  );
}
