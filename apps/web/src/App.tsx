import React, { useEffect, useState, useCallback, useMemo } from "react";
import { DndContext, DragOverlay, closestCenter, PointerSensor, useSensor, useSensors, type DragEndEvent, type DragStartEvent, type DragOverEvent } from "@dnd-kit/core";
import { SortableContext, arrayMove, rectSortingStrategy } from "@dnd-kit/sortable";

import { fetchLatest, saveDeviceOrder, logObservation, resolveAdjustment, type LatestReading, type Command, type DeviceEvent, type Device, type ObservationCategory, type RuleSuggestion, RelayStatus } from './api';
import { DevicePanel } from "./components/DevicePanel";
import { DeviceDiscoveryState } from "./components/DeviceDiscoveryState";
import { ActivityCenter, type ErrorItem } from "./components/ActivityCenter";
import { ObservationForm } from "./components/ObservationForm";
import { ChatInput } from "./components/ChatInput";
import { useWebSocket } from "./hooks/useWebSocket";

type DiscoveryPhase = "discovering" | "complete";

export function App(): React.ReactElement {
  // Multi-device state
  const [devices, setDevices] = useState<Device[]>([]);
  const [discoveryPhase, setDiscoveryPhase] = useState<DiscoveryPhase>("discovering");
  const [latestByDevice, setLatestByDevice] = useState<Record<string, LatestReading>>({});

  const [commands, setCommands] = useState<Command[]>([]);
  const [events, setEvents] = useState<DeviceEvent[]>([]);
  const [errors, setErrors] = useState<ErrorItem[]>([]);
  const [suggestions, setSuggestions] = useState<RuleSuggestion[]>([]);
  const [wsRelayUpdates, setWsRelayUpdates] = useState<RelayStatus[] | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [showObservationForm, setShowObservationForm] = useState(false);

  const addError = useCallback((message: string, source?: string) => {
    const error: ErrorItem = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
      ts: Date.now(),
      message,
      source,
    };
    setErrors((prev) => [error, ...prev].slice(0, 20)); // Keep last 20 errors
  }, []);

  const handleResolveAdjustment = useCallback(async (id: string, action: "approve" | "reject") => {
    const ok = await resolveAdjustment(id, action);
    if (ok) {
      // Optimistic update — WS broadcast will confirm
      setSuggestions((prev) =>
        prev.map((s) =>
          s.id === id
            ? { ...s, status: action === "approve" ? "applied" as const : "rejected" as const, resolvedAt: Date.now() }
            : s
        )
      );
    }
  }, []);

  // DnD sensors — distance threshold prevents triggering on clicks/taps
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } })
  );
  const [activeId, setActiveId] = useState<string | null>(null);

  const sortedDevices = useMemo(
    () => [...devices].sort((a, b) => a.displayOrder - b.displayOrder),
    [devices]
  );

  const activeDevice = useMemo(
    () => (activeId ? sortedDevices.find((d) => d.id === activeId) ?? null : null),
    [activeId, sortedDevices]
  );

  const handleDragStart = useCallback((event: DragStartEvent) => {
    setActiveId(String(event.active.id));
  }, []);

  const handleDragOver = useCallback((event: DragOverEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    setDevices((prev) => {
      const sorted = [...prev].sort((a, b) => a.displayOrder - b.displayOrder);
      const oldIndex = sorted.findIndex((d) => d.id === active.id);
      const newIndex = sorted.findIndex((d) => d.id === over.id);
      if (oldIndex === -1 || newIndex === -1) return prev;
      const reordered = arrayMove(sorted, oldIndex, newIndex);
      return reordered.map((d, i) => ({ ...d, displayOrder: i }));
    });
  }, []);

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    setActiveId(null);
    // Persist current order to backend
    setDevices((prev) => {
      const sorted = [...prev].sort((a, b) => a.displayOrder - b.displayOrder);
      saveDeviceOrder(sorted.map((d) => d.id)).catch(console.error);
      return prev;
    });
  }, []);

  // WebSocket connection for real-time updates
  const { isConnected } = useWebSocket({
    onLatestReading: (reading) => {
      // Store per-device latest readings
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
      // Sync relay state and update command status from command_ack events
      if (event.eventType === "command_ack" && event.data) {
        const { correlationId, status, actualValue } = event.data as {
          correlationId?: string;
          status?: string;
          actualValue?: boolean;
        };
        // Update the matching command's status so Recent Activity shows the ACK
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
    onSuggestionsUpdate: (suggestionList) => {
      setSuggestions(suggestionList);
    },
    // Connection status is shown in UI, no need to add errors to feed
  });

  // Fetch initial latest reading on mount (other data comes via WebSocket)
  useEffect(() => {
    const controller = new AbortController();

    async function fetchInitialData() {
      try {
        const l = await fetchLatest(controller.signal);
        if (l && l.deviceId) {
          setLatestByDevice((prev) => ({
            ...prev,
            [l.deviceId!]: l,
          }));
        }
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") return;
        console.error("Failed to fetch initial data:", error);
        addError("Failed to fetch initial data", "API");
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

  const hasActivity = commands.length > 0 || events.length > 0 || errors.length > 0 || suggestions.length > 0;

  return (
    <div className="min-h-screen w-full flex flex-col">
      {/* Header */}
      <header className="sticky top-0 z-40 flex items-center justify-between px-4 py-3 md:px-6 backdrop-blur-[10px] bg-black/10 border-b border-panel-border">
        <span className="text-sm font-medium tracking-wide opacity-60">Mycelium</span>
        <button
          onClick={() => setDrawerOpen((o) => !o)}
          className="relative p-2.5 rounded-xl border border-panel-border bg-panel/80 hover:bg-panel transition-colors cursor-pointer"
          aria-label="Toggle recent activity"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          {hasActivity && (
            <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-emerald-400" />
          )}
        </button>
      </header>

      {/* Main content area */}
      <div className="flex-1 w-full flex justify-center px-3 py-5 pb-40 md:px-5 md:pb-24">
        <div className="w-full">
          {/* Device panels section */}
          <div className="flex-1 min-w-0 flex flex-wrap justify-left flex-row gap-5">
            {/* Discovery state or device panels */}
            {discoveryPhase === "discovering" && devices.length === 0 ? (
              <DeviceDiscoveryState />
            ) : sortedDevices.length > 0 ? (
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={handleDragStart} onDragOver={handleDragOver} onDragEnd={handleDragEnd}>
                <SortableContext items={sortedDevices.map((d) => d.id)} strategy={rectSortingStrategy}>
                  {sortedDevices.map((device) => (
                    <DevicePanel
                      key={device.id}
                      device={device}
                      latestReading={latestByDevice[device.id] || null}
                      commands={commands}
                      isConnected={isConnected}
                      wsRelayUpdates={wsRelayUpdates}
                      onError={addError}
                    />
                  ))}
                </SortableContext>
                <DragOverlay dropAnimation={null}>
                  {activeDevice && (
                    <DevicePanel
                      device={activeDevice}
                      latestReading={latestByDevice[activeDevice.id] || null}
                      commands={commands}
                      isConnected={isConnected}
                      wsRelayUpdates={wsRelayUpdates}
                      onError={addError}
                      isOverlay
                    />
                  )}
                </DragOverlay>
              </DndContext>
            ) : (
              <div className="flex-1 min-w-0 border border-panel-border rounded-2xl p-5 backdrop-blur-[10px] flex items-center justify-center min-h-[200px]">
                <div className="text-sm opacity-60">
                  No devices found on the network. Make sure your devices are powered on and connected.
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Drawer backdrop */}
      <div
        className={`fixed inset-0 top-[53px] z-30 bg-black/40 transition-opacity duration-300 ${drawerOpen ? "opacity-100" : "opacity-0 pointer-events-none"}`}
        onClick={() => setDrawerOpen(false)}
      />

      {/* Drawer panel */}
      <div
        className={`fixed top-[53px] right-0 z-30 bottom-0 w-[340px] max-w-[85vw] backdrop-blur-[12px] bg-black/60 border-l border-panel-border transition-transform duration-300 ${drawerOpen ? "translate-x-0" : "translate-x-full"}`}
      >
        <div className="flex items-center justify-between pt-5 px-5">
          <h2 className="text-sm font-medium opacity-80">Activity Center</h2>
          <button
            onClick={() => setShowObservationForm((prev) => !prev)}
            className="p-1.5 rounded-lg border border-panel-border bg-panel/80 hover:bg-panel transition-colors cursor-pointer"
            aria-label="Log observation"
            title="Log observation"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
        </div>
        {showObservationForm && (
          <div className="px-5 pt-3">
            <ObservationForm
              devices={devices}
              onSubmit={async (obs) => {
                await logObservation(obs);
                setShowObservationForm(false);
              }}
              onCancel={() => setShowObservationForm(false)}
            />
          </div>
        )}
        <div className="h-full pt-4 px-5 pb-24">
          {hasActivity ? (
            <ActivityCenter commands={commands} events={events} errors={errors} suggestions={suggestions} onResolveAdjustment={handleResolveAdjustment} maxItems={20} />
          ) : (
            <div className="text-sm opacity-60">No recent activity</div>
          )}
        </div>
      </div>

      {/* Chat input - pinned to bottom */}
      <div className="fixed bottom-0 left-0 right-0 z-40 backdrop-blur-md border-t border-panel-border p-4 bg-black/20 dark:bg-black/40">
        <div className="max-w-[1400px] mx-auto">
          <ChatInput />
        </div>
      </div>
    </div>
  );
}