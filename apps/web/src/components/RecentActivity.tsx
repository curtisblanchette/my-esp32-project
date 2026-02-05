import React from "react";
import type { Command, DeviceEvent } from "../api";

export type ErrorItem = {
  id: string;
  ts: number;
  message: string;
  source?: string;
};

type ActivityItem = {
  id: string;
  ts: number;
  type: "command" | "event" | "error";
  source: string;
  description: string;
  status?: string;
  reason?: string | null;
};

type RecentActivityProps = {
  commands: Command[];
  events: DeviceEvent[];
  errors?: ErrorItem[];
  maxItems?: number;
};

function formatTime(ts: number): string {
  const date = new Date(ts);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function getSourceBadge(source: string): { label: string; className: string } {
  if (source === "ai-orchestrator") {
    return {
      label: "AI",
      className: "bg-purple-500/20 text-purple-400 border-purple-500/30",
    };
  }
  if (source === "dashboard") {
    return {
      label: "Manual",
      className: "bg-blue-500/20 text-blue-400 border-blue-500/30",
    };
  }
  if (source === "device") {
    return {
      label: "Device",
      className: "bg-green-500/20 text-green-400 border-green-500/30",
    };
  }
  if (source === "error") {
    return {
      label: "Error",
      className: "bg-red-500/20 text-red-400 border-red-500/30",
    };
  }
  return {
    label: source,
    className: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  };
}

function getStatusIcon(status: string): React.ReactElement {
  switch (status) {
    case "acked":
      return (
        <svg className="w-3.5 h-3.5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      );
    case "failed":
      return (
        <svg className="w-3.5 h-3.5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      );
    case "pending":
      return (
        <svg className="w-3.5 h-3.5 text-yellow-500 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
      );
    default:
      return <div className="w-3.5 h-3.5" />;
  }
}

export function RecentActivity({ commands, events, errors = [], maxItems = 5 }: RecentActivityProps): React.ReactElement {
  // Merge and sort commands, events, and errors
  const items: ActivityItem[] = [
    ...commands.map((cmd) => ({
      id: `cmd-${cmd.id}`,
      ts: cmd.ts,
      type: "command" as const,
      source: cmd.source,
      description: `${cmd.target} ${cmd.action} = ${String(cmd.value)}`,
      status: cmd.status,
      reason: cmd.reason,
    })),
    ...events
      .filter((e) => e.eventType !== "command_ack") // Don't duplicate ack info
      .map((evt) => ({
        id: `evt-${evt.id}`,
        ts: evt.ts,
        type: "event" as const,
        source: evt.source || "system",
        description: formatEventDescription(evt),
        status: undefined,
        reason: null,
      })),
    ...errors.map((err) => ({
      id: `err-${err.id}`,
      ts: err.ts,
      type: "error" as const,
      source: "error",
      description: err.message,
      status: "failed",
      reason: err.source || null,
    })),
  ];

  items.sort((a, b) => b.ts - a.ts);
  const displayItems = items.slice(0, maxItems);

  if (displayItems.length === 0) {
    return (
      <div className="p-4 text-center text-sm opacity-50">
        No recent activity
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {displayItems.map((item) => {
        const badge = getSourceBadge(item.source);
        return (
          <div
            key={item.id}
            className="flex items-start gap-3 p-2 rounded-lg bg-panel-bg/30 border border-panel-border/50"
          >
            <div className="flex-shrink-0 mt-0.5">
              {item.type === "error" ? (
                <svg className="w-3.5 h-3.5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              ) : item.type === "command" && item.status ? (
                getStatusIcon(item.status)
              ) : (
                <div className="w-3.5 h-3.5 rounded-full bg-gray-500/50" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span
                  className={`px-1.5 py-0.5 text-[10px] font-medium rounded border ${badge.className}`}
                >
                  {badge.label}
                </span>
                <span className="text-xs opacity-50">{formatTime(item.ts)}</span>
              </div>
              <div className="text-sm mt-0.5 truncate">{item.description}</div>
              {item.reason && (
                <div className="text-xs opacity-50 mt-0.5 truncate" title={item.reason}>
                  {item.reason}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function formatEventDescription(event: DeviceEvent): string {
  switch (event.eventType) {
    case "device_birth":
      return `Device ${event.deviceId} came online`;
    case "device_offline":
      return `Device ${event.deviceId} went offline`;
    default:
      return `${event.eventType} (${event.deviceId})`;
  }
}
