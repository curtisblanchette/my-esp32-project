import React, { useEffect, useMemo } from 'react';
import { useSortable } from "@dnd-kit/sortable";
import { SensorCard } from "./SensorCard";
import { RelayControl } from "./RelayControl";
import { AIStatusIndicator } from "./AIStatusIndicator";
import {
  type Device,
  type LatestReading,
  type RelayStatus,
  type Command,
  hasSensors,
  hasActuators,
} from "../api";
import { useRelays } from '../hooks/useRelays';

interface DevicePanelProps {
  device: Device;
  latestReading: LatestReading | null;
  commands: Command[];
  isConnected: boolean;
  wsRelayUpdates: RelayStatus[] | null;
  onError: (message: string, source?: string) => void;
  isOverlay?: boolean;
}

export function DevicePanel(props: DevicePanelProps): React.ReactElement {
  const {
    device,
    latestReading,
    commands,
    isConnected,
    wsRelayUpdates,
    onError,
    isOverlay,
  } = props;

  const sortable = useSortable({ id: device.id, disabled: isOverlay });
  const { attributes, listeners, setNodeRef, isDragging } = sortable;

  // No CSS transform — items reorder via React re-render to preserve backdrop-blur fidelity
  const sortableStyle: React.CSSProperties = isOverlay
    ? {}
    : isDragging ? { opacity: 0.3 } : {};

  const { relays, applyRelays, handleStateChange, handleNameChange } = useRelays(device.id);

  // Check if AI is active (had commands for this device in last 5 minutes)
  const aiStatus = useMemo(() => {
    const aiCommands = commands.filter(
      (c) => c.source === "ai-orchestrator" && c.deviceId === device.id
    );
    const fiveMinutesAgo = Date.now() - 5 * 60 * 1000;
    const recentAiCommand = aiCommands.find((c) => c.ts > fiveMinutesAgo);
    return {
      isActive: !!recentAiCommand,
      lastCommandTs: aiCommands.length > 0 ? aiCommands[0].ts : null,
    };
  }, [commands, device.id]);

  useEffect(() => {
    if(wsRelayUpdates) {
      const mine = wsRelayUpdates.filter(r => r.deviceId === device.id);
      if (mine.length > 0) {
        applyRelays(mine);
      }
    }
  }, [wsRelayUpdates, device.id, applyRelays]);
  const hasOfflineDevice = !device.online;
  const showSensors = hasSensors(device);
  const showRelays = hasActuators(device);

  return (
    <div
      ref={isOverlay ? undefined : setNodeRef}
      style={sortableStyle}
      className="flex-1 min-w-[390px] border max-w-[500px] border-panel-border rounded-2xl p-5 backdrop-blur-[10px] h-[fit-content]"
    >
      {/* Device header */}
      <div className="w-full flex justify-between items-center mb-6">
        <div className="flex items-center gap-3">
          {/* Drag handle */}
          <button
            className="cursor-grab active:cursor-grabbing p-1 -ml-1 rounded hover:bg-white/10 transition-colors touch-none"
            aria-label="Drag to reorder"
            {...attributes}
            {...listeners}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" className="opacity-40">
              <circle cx="9" cy="6" r="1.5" />
              <circle cx="15" cy="6" r="1.5" />
              <circle cx="9" cy="12" r="1.5" />
              <circle cx="15" cy="12" r="1.5" />
              <circle cx="9" cy="18" r="1.5" />
              <circle cx="15" cy="18" r="1.5" />
            </svg>
          </button>
          <img src="/microcontroller.png" alt="" className="w-10 h-10" />
          <div>
            <h1 className="m-0 text-xl font-semibold">{device.name || device.id}</h1>
            <div className="text-xs opacity-60">{device.location}</div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <AIStatusIndicator
            isActive={aiStatus.isActive}
            lastCommandTs={aiStatus.lastCommandTs}
          />
          <div className="flex items-center gap-1.5 text-xs">
            <div
              className={`w-2 h-2 rounded-full ${
                device.online
                  ? isConnected
                    ? "bg-green-500"
                    : "bg-yellow-500"
                  : "bg-red-500"
              }`}
            />
            <span className="opacity-60">
              {device.online
                ? isConnected
                  ? "Live"
                  : "Connecting..."
                : "Offline"}
            </span>
          </div>
        </div>
      </div>

      {/* Sensor gauges and charts (any sensor types) */}
      {showSensors && (
        <div className="mt-3">
          <h2 className="text-sm font-medium opacity-80 mb-2">Sensors</h2>
          <SensorCard
            device={device}
            latestReading={latestReading}
          />
        </div>
      )}

      {/* Relay controls (only if device has actuators) */}
      {showRelays && (
        <div className="mt-3">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-medium opacity-80">Actuators</h2>
            {hasOfflineDevice && (
              <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-yellow-500/10 border border-yellow-500/30 text-yellow-600 dark:text-yellow-500 text-xs">
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                  />
                </svg>
                <span>Device is offline</span>
              </div>
            )}
          </div>
          {relays.length > 0 ? (
            <div className="flex flex-wrap gap-3">
              {relays.map((relay: RelayStatus) => (
                <RelayControl
                  deviceId={device.id}
                  key={relay.id}
                  relay={relay}
                  onStateChange={handleStateChange}
                  onNameChange={handleNameChange}
                  onError={onError}
                />
              ))}
            </div>
          ) : (
            <div className="text-sm opacity-60">
              No actuators configured for this device.
            </div>
          )}
        </div>
      )}

      {/* Show message if device has no sensors or actuators */}
      {!showSensors && !showRelays && (
        <div className="mt-3 text-sm opacity-60">
          This device has no sensors or actuators configured.
        </div>
      )}

    </div>
  );
}
