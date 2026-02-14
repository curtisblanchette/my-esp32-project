import React from "react";
import { OBSERVATION_CATEGORIES, type Command, type DeviceEvent, type RuleSuggestion } from "../api";

export type ErrorItem = {
  id: string;
  ts: number;
  message: string;
  source?: string;
};

type ActivityItem = {
  id: string;
  ts: number;
  type: "command" | "event" | "error" | "suggestion";
  source: string;
  description: string;
  status?: string;
  reason?: string | null;
  suggestionId?: string;
  confidence?: number;
};

type RecentActivityProps = {
  commands: Command[];
  events: DeviceEvent[];
  errors?: ErrorItem[];
  suggestions?: RuleSuggestion[];
  onResolveAdjustment?: (id: string, action: "approve" | "reject") => void;
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
  if (source === "human") {
    return {
      label: "Human",
      className: "bg-amber-500/20 text-amber-400 border-amber-500/30",
    };
  }
  if (source === "cortex") {
    return {
      label: "Cortex",
      className: "bg-teal-500/20 text-teal-400 border-teal-500/30",
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

function getSuggestionIcon(status?: string): React.ReactElement {
  switch (status) {
    case "applied":
      return (
        <svg className="w-3.5 h-3.5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      );
    case "rejected":
      return (
        <svg className="w-3.5 h-3.5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      );
    default:
      // Pending — lightbulb/idea icon
      return (
        <svg className="w-3.5 h-3.5 text-teal-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
      );
  }
}

function formatSuggestionDescription(s: RuleSuggestion): string {
  const change = `${s.ruleName}.${s.field}: ${s.currentValue} → ${s.suggestedValue}`;
  switch (s.status) {
    case "applied":
      return `Applied: ${change}`;
    case "rejected":
      return `Rejected: ${change}`;
    default:
      return change;
  }
}

export function ActivityCenter({ commands, events, errors = [], suggestions = [], onResolveAdjustment, maxItems = 5 }: RecentActivityProps): React.ReactElement {
  // Merge and sort commands, events, errors, and suggestions
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
    ...suggestions.map((s) => ({
      id: `sug-${s.id}`,
      ts: s.resolvedAt || s.createdAt,
      type: "suggestion" as const,
      source: "cortex",
      description: formatSuggestionDescription(s),
      status: s.status,
      reason: s.reason,
      suggestionId: s.id,
      confidence: s.confidence,
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
    <div className="space-y-2 overflow-y-scroll">
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
              ) : item.type === "suggestion" ? (
                getSuggestionIcon(item.status)
              ) : item.type === "command" && item.status ? (
                getStatusIcon(item.status)
              ) : item.source === "human" ? (
                <svg className="w-3.5 h-3.5 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                </svg>
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
                {item.confidence != null && (
                  <span className="px-1.5 py-0.5 text-[10px] rounded bg-white/5 text-white/50">
                    {Math.round(item.confidence * 100)}%
                  </span>
                )}
                <span className="text-xs opacity-50">{formatTime(item.ts)}</span>
              </div>
              <div className="text-sm mt-0.5 truncate">{item.description}</div>
              {item.reason && (
                <div className="text-xs opacity-50 mt-0.5 truncate" title={item.reason}>
                  {item.reason}
                </div>
              )}
              {item.type === "suggestion" && item.status === "pending" && item.suggestionId && onResolveAdjustment && (
                <div className="flex gap-2 mt-1.5">
                  <button
                    onClick={() => onResolveAdjustment(item.suggestionId!, "approve")}
                    className="px-2 py-0.5 text-[10px] font-medium rounded border border-green-500/30 bg-green-500/10 text-green-400 hover:bg-green-500/20 cursor-pointer transition-colors"
                  >
                    Approve
                  </button>
                  <button
                    onClick={() => onResolveAdjustment(item.suggestionId!, "reject")}
                    className="px-2 py-0.5 text-[10px] font-medium rounded border border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20 cursor-pointer transition-colors"
                  >
                    Reject
                  </button>
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
    case "observation": {
      const category = event.data?.category as string | undefined;
      const notes = event.data?.notes as string | undefined;
      const label = OBSERVATION_CATEGORIES.find((c) => c.key === category)?.label ?? category ?? "Observation";
      return notes ? `${label}: ${notes}` : label;
    }
    default:
      return `${event.eventType} (${event.deviceId})`;
  }
}
