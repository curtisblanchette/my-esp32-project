import React, { useEffect, useState, useCallback, useRef } from "react";
import { Routes, Route, useNavigate, useLocation } from "react-router-dom";

import { fetchLatest, type LatestReading, type Command, type DeviceEvent, type Device, RelayStatus } from './api';
import { ActivityCenter, type ErrorItem } from "./components/ActivityCenter";
import { useWebSocket } from "./hooks/useWebSocket";
import { Home } from "./pages/Home";
import { Devices } from "./pages/Devices";
import { NerveCenter } from "./pages/NerveCenter";

type DiscoveryPhase = "discovering" | "complete";

export function App(): React.ReactElement {
  const navigate = useNavigate();
  const location = useLocation();

  // Multi-device state
  const [devices, setDevices] = useState<Device[]>([]);
  const [discoveryPhase, setDiscoveryPhase] = useState<DiscoveryPhase>("discovering");
  const [latestByDevice, setLatestByDevice] = useState<Record<string, LatestReading>>({});

  const [commands, setCommands] = useState<Command[]>([]);
  const [events, setEvents] = useState<DeviceEvent[]>([]);
  const [errors, setErrors] = useState<ErrorItem[]>([]);
  const [wsRelayUpdates, setWsRelayUpdates] = useState<RelayStatus[] | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [unseenCount, setUnseenCount] = useState(0);
  const lastSeenCountRef = useRef(0);

  const addError = useCallback((message: string, source?: string) => {
    const error: ErrorItem = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
      ts: Date.now(),
      message,
      source,
    };
    setErrors((prev) => [error, ...prev].slice(0, 20));
  }, []);

  // WebSocket connection for real-time updates
  const { isConnected } = useWebSocket({
    onLatestReading: (reading) => {
      if (reading.deviceId) {
        setLatestByDevice((prev) => ({
          ...prev,
          [reading.deviceId!]: reading,
        }));
      }
    },
    onRelayUpdate: (relayList) => {
      setWsRelayUpdates(relayList);
    },
    onDevicesUpdate: (deviceList) => {
      setDevices(deviceList);
      setDiscoveryPhase("complete");
    },
    onEventsUpdate: (eventList) => {
      setEvents(eventList);
    },
    onEventReceived: (event) => {
      setEvents((prev) => [event, ...prev.filter((e) => e.id !== event.id)].slice(0, 20));
      if (event.eventType === "command_ack" && event.data) {
        const { correlationId, status, actualValue } = event.data as {
          correlationId?: string;
          status?: string;
          actualValue?: boolean;
        };
        if (correlationId && status) {
          setCommands((prev) =>
            prev.map((cmd) =>
              cmd.id === correlationId
                ? { ...cmd, status: status as Command["status"], ackedAt: event.ts, actualValue }
                : cmd
            )
          );
        }
      }
    },
    onCommandsUpdate: (commandList) => {
      setCommands(commandList);
    },
    onCommandReceived: (command) => {
      setCommands((prev) => {
        const idx = prev.findIndex((c) => c.id === command.id);
        if (idx >= 0) {
          const updated = [...prev];
          updated[idx] = command;
          return updated;
        }
        return [command, ...prev].slice(0, 20);
      });
    },
  });

  // Fetch initial latest reading on mount (retries while Cortex starts up)
  useEffect(() => {
    const controller = new AbortController();
    let attempt = 0;
    const maxRetries = 10;
    const baseDelay = 2000;

    async function fetchInitialData() {
      while (attempt < maxRetries && !controller.signal.aborted) {
        try {
          const l = await fetchLatest(controller.signal);
          if (l && l.deviceId) {
            setLatestByDevice((prev) => ({
              ...prev,
              [l.deviceId!]: l,
            }));
          }
          return;
        } catch (error) {
          if (error instanceof Error && error.name === "AbortError") return;
          attempt++;
          if (attempt >= maxRetries) {
            console.error("Failed to fetch initial data after retries:", error);
            addError("Failed to fetch initial data", "API");
          } else {
            await new Promise((r) => setTimeout(r, baseDelay * attempt));
          }
        }
      }
    }

    fetchInitialData();

    return () => {
      controller.abort();
    };
  }, [addError]);

  // Close drawer on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  const totalActivityCount = commands.length + events.length + errors.length;
  const hasActivity = totalActivityCount > 0;

  // Track unseen activity
  useEffect(() => {
    if (drawerOpen) {
      lastSeenCountRef.current = totalActivityCount;
      setUnseenCount(0);
    } else {
      const newItems = totalActivityCount - lastSeenCountRef.current;
      if (newItems > 0) setUnseenCount(newItems);
    }
  }, [drawerOpen, totalActivityCount]);
  const isDevices = location.pathname === "/devices";
  const isNerveCenter = location.pathname === "/nerve-center";

  return (
    <div className="min-h-screen w-full flex flex-col">
      {/* Header */}
      <header className="sticky top-0 z-40 relative flex items-center justify-center px-4 py-4 md:px-6 backdrop-blur-[10px] bg-black/10 border-b border-panel-border">
        <nav className="flex items-center gap-5">
          <span
            className="flex items-center gap-1.5 text-sm font-medium tracking-wide opacity-60 cursor-pointer hover:opacity-80 transition-opacity"
            onClick={() => navigate("/")}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
              <polyline points="9 22 9 12 15 12 15 22" />
            </svg>
            Home
          </span>
          <button
            onClick={() => navigate("/devices")}
            className={`flex items-center gap-1.5 text-sm cursor-pointer transition-opacity ${
              isDevices ? "opacity-90 font-medium" : "opacity-50 hover:opacity-70"
            }`}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="4" y="4" width="16" height="16" rx="2" />
              <rect x="9" y="9" width="6" height="6" />
              <path d="M15 2v2" /><path d="M15 20v2" /><path d="M2 15h2" /><path d="M2 9h2" />
              <path d="M20 15h2" /><path d="M20 9h2" /><path d="M9 2v2" /><path d="M9 20v2" />
            </svg>
            Devices
          </button>
          <button
            onClick={() => navigate("/nerve-center")}
            className={`flex items-center gap-1.5 text-sm cursor-pointer transition-opacity ${
              isNerveCenter ? "opacity-90 font-medium" : "opacity-50 hover:opacity-70"
            }`}
          >
            <img src="/nerve_center.svg" alt="" width="15" height="15" className="opacity-80" />
            Nerve Center
          </button>
        </nav>
        <div className="absolute right-4 md:right-6">
          <button
            onClick={() => setDrawerOpen((o) => !o)}
            className={`relative p-2.5 rounded-xl border transition-colors cursor-pointer ${
              drawerOpen
                ? "border-white/20 bg-white/10"
                : "border-panel-border bg-panel/80 hover:bg-panel"
            }`}
            aria-label="Toggle recent activity"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`transition-opacity ${drawerOpen ? "opacity-90" : "opacity-60"}`}>
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            {unseenCount > 0 && !drawerOpen && (
              <span className="absolute top-1 right-1 min-w-[14px] h-[14px] flex items-center justify-center px-0.5 rounded-full bg-emerald-500 text-[9px] font-semibold text-white">
                {unseenCount > 9 ? "9+" : unseenCount}
              </span>
            )}
          </button>
        </div>
      </header>

      {/* Route content */}
      <Routes>
        <Route path="/" element={<Home devices={devices} />} />
        <Route
          path="/devices"
          element={
            <Devices
              devices={devices}
              setDevices={setDevices}
              discoveryPhase={discoveryPhase}
              latestByDevice={latestByDevice}
              commands={commands}
              isConnected={isConnected}
              wsRelayUpdates={wsRelayUpdates}
              addError={addError}
            />
          }
        />
        <Route
          path="/nerve-center"
          element={
            <NerveCenter
              devices={devices}
              addError={addError}
            />
          }
        />
      </Routes>

      {/* Drawer backdrop */}
      <div
        className={`fixed inset-0 z-30 bg-black/40 transition-opacity duration-300 ${drawerOpen ? "opacity-100" : "opacity-0 pointer-events-none"}`}
        onClick={() => setDrawerOpen(false)}
      />

      {/* Drawer panel */}
      <div
        className={`fixed top-[53px] right-0 z-30 bottom-0 w-[340px] max-w-[85vw] backdrop-blur-[12px] bg-black/60 border-l border-panel-border transition-transform duration-300 flex flex-col ${drawerOpen ? "translate-x-0" : "translate-x-full"}`}
      >
        <div className="pt-5 px-5">
          <h2 className="text-sm font-medium opacity-80">Activity Center</h2>
        </div>
        <div className="relative flex-1 min-h-0">
          <div className="h-full overflow-y-auto overflow-x-hidden pt-4 px-5 pb-32">
            {hasActivity ? (
              <ActivityCenter commands={commands} events={events} errors={errors} maxItems={20} />
            ) : (
              <div className="text-sm opacity-60">No recent activity</div>
            )}
          </div>
          <div className="pointer-events-none absolute bottom-0 left-0 right-0 h-24 bg-gradient-to-t from-black/60 to-transparent" />
        </div>
      </div>

    </div>
  );
}