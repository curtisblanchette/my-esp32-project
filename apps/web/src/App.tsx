import React, { useEffect, useMemo, useState } from "react";

import { fetchLatest, type LatestReading } from "./api";
import { MetricCard } from "./components/MetricCard";
import { RelayControl } from "./components/RelayControl";
import { Toast } from "./components/Toast";
import { useWebSocket } from "./hooks/useWebSocket";
import { useToast } from "./hooks/useToast";
import { useRelays } from "./hooks/useRelays";
import { fmtTime } from "./lib/format";

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

function tempToMix(tC: number): number {
  const minC = 8;
  const maxC = 30;
  const x = clamp((tC - minC) / (maxC - minC), 0, 1);
  const eased = Math.pow(x, 0.65);
  return clamp(0.12 + eased * 0.88, 0, 1);
}

export function App(): React.ReactElement {
  const [latest, setLatest] = useState<LatestReading | null>(null);
  const { toasts, showToast, removeToast } = useToast();
  const { relays, isInMockMode, applyRelays, handleStateChange, handleNameChange } = useRelays();

  // WebSocket connection for real-time updates
  const { isConnected } = useWebSocket({
    onLatestReading: (reading) => {
      setLatest(reading);
    },
    onRelayUpdate: (relayList) => {
      applyRelays(relayList);
    },
    onError: (error) => {
      console.error("WebSocket error:", error);
    },
  });

  // Fallback: fetch initial data if WebSocket hasn't connected yet
  useEffect(() => {
    const controller = new AbortController();

    async function fetchInitialData() {
      try {
        const l = await fetchLatest(controller.signal);
        if (l) setLatest(l);
      } catch (error) {
        console.error("Failed to fetch initial data:", error);
      }
    }

    fetchInitialData();

    return () => {
      controller.abort();
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
    <>
      <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-md">
        {toasts.map((toast) => (
          <Toast
            key={toast.id}
            message={toast.message}
            type={toast.type}
            onClose={() => removeToast(toast.id)}
          />
        ))}
      </div>

      <div className="w-full max-w-[960px] border border-panel-border rounded-2xl p-5 backdrop-blur-[10px]">
        <div className="w-full flex justify-between">
          <h1 className="m-0 mb-3 text-xl">Sensor Dashboard</h1>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5 text-xs">
              <div className={`w-2 h-2 rounded-full ${isConnected ? "bg-green-500" : "bg-gray-400"}`} />
              <span className="opacity-60">{isConnected ? "Live" : "Connecting..."}</span>
            </div>
            <div className="flex items-center gap-2">
              <img src="/microcontroller.png" alt="" className="w-[36px] h-[36px]" />
              esp32-1
            </div>
          </div>
        </div>

        <div className="flex flex-wrap gap-3 mt-3">
          <MetricCard
            type="temperature"
            currentValue={derived?.t ?? null}
            note={derived ? derived.tempNote : "Waiting…"}
            subtitle="Cool → Warm"
            mix={derived?.mix}
            latestReading={latest}
          />
          <MetricCard
            type="humidity"
            currentValue={derived?.h ?? null}
            note={derived ? derived.humidityNote : "Waiting…"}
            subtitle={derived ? derived.humiditySub : "--"}
            latestReading={latest}
          />
        </div>

        {relays.length > 0 && (
          <div className="mt-3">
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-sm font-medium opacity-80">Relay Controls</h2>
              {isInMockMode && (
                <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-yellow-500/10 border border-yellow-500/30 text-yellow-600 dark:text-yellow-500 text-xs">
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                    />
                  </svg>
                  <span>Preview Mode - Changes won't be saved</span>
                </div>
              )}
            </div>
            <div className="flex flex-wrap gap-3">
              {relays.map((relay) => (
                <RelayControl
                  key={relay.id}
                  relay={relay}
                  onStateChange={handleStateChange}
                  onNameChange={handleNameChange}
                  onError={showToast}
                />
              ))}
            </div>
          </div>
        )}

        <div className="mt-3 text-xs opacity-75">
          {latest ? `Last update: ${fmtTime(latest.updatedAt)}` : "Waiting for first reading..."}
        </div>
      </div>
    </>
  );
}