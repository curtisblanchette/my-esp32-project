import React, { useEffect, useState, useCallback } from "react";

import { fetchLatest, type LatestReading, type Command, type DeviceEvent, type Device } from "./api";
import { DevicePanel } from "./components/DevicePanel";
import { DeviceDiscoveryState } from "./components/DeviceDiscoveryState";
import { RecentActivity, type ErrorItem } from "./components/RecentActivity";
import { ChatInput } from "./components/ChatInput";
import { useWebSocket } from "./hooks/useWebSocket";
import { useRelays } from "./hooks/useRelays";

type DiscoveryPhase = "discovering" | "complete";

export function App(): React.ReactElement {
  // Multi-device state
  const [devices, setDevices] = useState<Device[]>([]);
  const [discoveryPhase, setDiscoveryPhase] = useState<DiscoveryPhase>("discovering");
  const [latestByDevice, setLatestByDevice] = useState<Record<string, LatestReading>>({});

  const [commands, setCommands] = useState<Command[]>([]);
  const [events, setEvents] = useState<DeviceEvent[]>([]);
  const [errors, setErrors] = useState<ErrorItem[]>([]);
  const { relays, applyRelays, handleStateChange, handleNameChange } = useRelays();

  const addError = useCallback((message: string, source?: string) => {
    const error: ErrorItem = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
      ts: Date.now(),
      message,
      source,
    };
    setErrors((prev) => [error, ...prev].slice(0, 20)); // Keep last 20 errors
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
      applyRelays(relayList);
    },
    onDevicesUpdate: (deviceList) => {
      setDevices(deviceList);
      setDiscoveryPhase("complete");
    },
    onEventsUpdate: (eventList) => {
      setEvents(eventList);
    },
    onEventReceived: (event) => {
      setEvents((prev) => [event, ...prev].slice(0, 20));
      // Sync relay state and update command status from command_ack events
      if (event.eventType === "command_ack" && event.data) {
        const { correlationId, status, target, actualValue } = event.data as {
          correlationId?: string;
          status?: string;
          target?: string;
          actualValue?: boolean;
        };
        if (target && actualValue !== undefined) {
          handleStateChange(target, Boolean(actualValue));
        }
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
      setCommands((prev) => [command, ...prev].slice(0, 20));
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

  return (
    <div className="min-h-screen flex flex-col">
      {/* Main content area */}
      <div className="flex-1 w-screen flex justify-center p-5 pb-40 md:pb-24">
        <div className="w-full max-w-[1400px] flex flex-col md:flex-row md:items-start gap-5">
          {/* Device panels section */}
          <div className="flex-1 md:flex-[3] min-w-0 flex flex-col gap-5">
            {/* Discovery state or device panels */}
            {discoveryPhase === "discovering" && devices.length === 0 ? (
              <DeviceDiscoveryState />
            ) : devices.length > 0 ? (
              devices.map((device) => (
                <DevicePanel
                  key={device.id}
                  device={device}
                  latestReading={latestByDevice[device.id] || null}
                  relays={relays}
                  commands={commands}
                  isConnected={isConnected}
                  onRelayStateChange={handleStateChange}
                  onRelayNameChange={handleNameChange}
                  onError={addError}
                />
              ))
            ) : (
              <div className="flex-1 min-w-0 border border-panel-border rounded-2xl p-5 backdrop-blur-[10px] flex items-center justify-center min-h-[200px]">
                <div className="text-sm opacity-60">
                  No devices found on the network. Make sure your devices are powered on and connected.
                </div>
              </div>
            )}
          </div>

          {/* Recent Activity - sidebar */}
          <div className="w-full md:flex-1 border border-panel-border rounded-2xl p-5 backdrop-blur-[10px]">
            <h2 className="text-sm font-medium opacity-80 mb-3">Recent Activity</h2>
            {commands.length > 0 || events.length > 0 || errors.length > 0 ? (
              <RecentActivity commands={commands} events={events} errors={errors} maxItems={10} />
            ) : (
              <div className="text-sm opacity-60">No recent activity</div>
            )}
          </div>
        </div>
      </div>

      {/* Chat input - pinned to bottom */}
      <div className="fixed bottom-0 left-0 right-0 backdrop-blur-md border-t border-panel-border p-4 bg-black/20 dark:bg-black/40">
        <div className="max-w-[1400px] mx-auto px-3 md:px-5">
          <ChatInput />
        </div>
      </div>
    </div>
  );
}