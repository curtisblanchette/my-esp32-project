import React, { useMemo } from "react";
import { SensorCard } from "./SensorCard";
import { RelayControl } from "./RelayControl";
import { AIStatusIndicator } from "./AIStatusIndicator";
import {
  type Device,
  type LatestReading,
  type RelayStatus,
  type Command,
  hasTempHumiditySensors,
  hasActuators,
} from "../api";
import { fmtTime } from "../lib/format";

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

interface DevicePanelProps {
  device: Device;
  latestReading: LatestReading | null;
  relays: RelayStatus[];
  commands: Command[];
  isConnected: boolean;
  onRelayStateChange: (relayId: string, state: boolean) => void;
  onRelayNameChange: (relayId: string, name: string) => void;
  onError: (message: string, source?: string) => void;
}

export function DevicePanel(props: DevicePanelProps): React.ReactElement {
  const {
    device,
    latestReading,
    relays,
    commands,
    isConnected,
    onRelayStateChange,
    onRelayNameChange,
    onError,
  } = props;

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

  // Derive display values from latest reading
  const derived = useMemo(() => {
    if (!latestReading) return null;
    const t = Number(latestReading.temp);
    const h = clamp(Number(latestReading.humidity), 0, 100);
    const mix = tempToMix(t);

    const tempSub = t >= 25 ? "Warm" : t <= 18 ? "Cool" : "Comfortable";
    const humiditySub = h >= 70 ? "Humid" : h <= 35 ? "Dry" : "Comfortable";
    const tempNote = t >= 25 ? "Warm" : t <= 18 ? "Cool" : "Comfortable";
    const humidityNote =
      h >= 70 ? "Air feels heavy" : h <= 35 ? "Consider a humidifier" : "Nice range";

    return { t, h, mix, tempSub, humiditySub, tempNote, humidityNote };
  }, [latestReading]);

  // Filter relays for this device
  const deviceRelays = relays.filter((r) => r.deviceId === device.id);
  const hasOfflineDevice = !device.online;
  const showSensors = hasTempHumiditySensors(device);
  const showRelays = hasActuators(device);

  return (
    <div className="flex-1 min-w-0 border border-panel-border rounded-2xl p-5 backdrop-blur-[10px]">
      {/* Device header */}
      <div className="w-full flex justify-between items-center mb-3">
        <div className="flex items-center gap-3">
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

      {/* Sensor gauges and charts (only if device has temp/humidity sensors) */}
      {showSensors && (
        <div className="mt-3">
          <SensorCard
            temp={derived?.t ?? null}
            humidity={derived?.h ?? null}
            tempNote={derived ? derived.tempNote : "Waiting..."}
            tempSubtitle={derived ? derived.tempSub : "--"}
            humidityNote={derived ? derived.humidityNote : "Waiting..."}
            humiditySubtitle={derived ? derived.humiditySub : "--"}
            mix={derived?.mix}
            latestReading={latestReading}
            deviceId={device.id}
          />
        </div>
      )}

      {/* Relay controls (only if device has actuators) */}
      {showRelays && (
        <div className="mt-3">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-medium opacity-80">Relay Controls</h2>
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
          {deviceRelays.length > 0 ? (
            <div className="flex flex-wrap gap-3">
              {deviceRelays.map((relay) => (
                <RelayControl
                  key={relay.id}
                  relay={relay}
                  onStateChange={onRelayStateChange}
                  onNameChange={onRelayNameChange}
                  onError={onError}
                />
              ))}
            </div>
          ) : (
            <div className="text-sm opacity-60">
              No relays configured for this device.
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

      {/* Last update time */}
      <div className="mt-3 text-xs opacity-75">
        {latestReading
          ? `Last update: ${fmtTime(latestReading.updatedAt)}`
          : "Waiting for first reading..."}
      </div>
    </div>
  );
}
