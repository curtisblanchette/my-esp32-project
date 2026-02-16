import React, { useState } from "react";
import type { RuleSuggestion } from "../api";

type SuggestionsProps = {
  suggestions: RuleSuggestion[];
  onResolveAdjustment: (id: string, action: "approve" | "reject") => Promise<void>;
  onRunAdvisor: () => Promise<void>;
};

type StatusFilter = "all" | "pending" | "applied" | "rejected";

function formatTime(ts: number): string {
  const date = new Date(ts);
  return date.toLocaleDateString([], { month: "short", day: "numeric" }) +
    " " + date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-teal-500/20 text-teal-400 border-teal-500/30",
  applied: "bg-green-500/20 text-green-400 border-green-500/30",
  rejected: "bg-red-500/20 text-red-400 border-red-500/30",
};

export function NerveCenterSuggestions({ suggestions, onResolveAdjustment, onRunAdvisor }: SuggestionsProps): React.ReactElement {
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [advisorRunning, setAdvisorRunning] = useState(false);

  const filtered = suggestions.filter((s) => {
    if (filter === "all") return s.status !== "rejected";
    return s.status === filter;
  });

  // Sort: pending first, then by date descending
  const sorted = [...filtered].sort((a, b) => {
    if (a.status === "pending" && b.status !== "pending") return -1;
    if (a.status !== "pending" && b.status === "pending") return 1;
    return (b.resolvedAt || b.createdAt) - (a.resolvedAt || a.createdAt);
  });

  const counts = {
    all: suggestions.filter((s) => s.status !== "rejected").length,
    pending: suggestions.filter((s) => s.status === "pending").length,
    applied: suggestions.filter((s) => s.status === "applied").length,
    rejected: suggestions.filter((s) => s.status === "rejected").length,
  };

  return (
    <div className="space-y-3">
      {/* Header with filter + run button */}
      <div className="flex items-center justify-between">
        <div className="flex gap-2 text-xs">
          {(["all", "pending", "applied", "rejected"] as StatusFilter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2.5 py-1 rounded-lg border transition-colors cursor-pointer ${
                filter === f
                  ? "border-white/20 bg-white/10 text-white"
                  : "border-panel-border text-white/40 hover:text-white/60"
              }`}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)} ({counts[f]})
            </button>
          ))}
        </div>
        <button
          onClick={async () => {
            setAdvisorRunning(true);
            try { await onRunAdvisor(); } finally { setAdvisorRunning(false); }
          }}
          disabled={advisorRunning}
          className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-lg border border-teal-500/30 bg-teal-500/10 text-teal-400 hover:bg-teal-500/20 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer transition-colors"
        >
          {advisorRunning ? "Running..." : "Run Advisor"}
        </button>
      </div>

      {/* Suggestion list */}
      {sorted.length === 0 ? (
        <div className="text-sm opacity-50 text-center py-8">
          {filter === "all" ? "No suggestions yet. Run the advisor to analyze rule performance." : `No ${filter} suggestions`}
        </div>
      ) : (
        sorted.map((s) => (
          <div
            key={s.id}
            className="rounded-xl border border-panel-border bg-panel/30 backdrop-blur-[6px] p-4 space-y-2"
          >
            {/* Header */}
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded border ${STATUS_STYLES[s.status] || ""}`}>
                {s.status}
              </span>
              <span className="text-sm font-medium">{s.ruleName}</span>
              <span className="text-xs opacity-40">{s.field}</span>
              <span className="px-1.5 py-0.5 text-[10px] rounded bg-white/5 text-white/50">
                {Math.round(s.confidence * 100)}% confidence
              </span>
            </div>

            {/* Change */}
            <div className="text-sm">
              <span className="opacity-50">{s.currentValue}</span>
              <span className="opacity-30 mx-2">&rarr;</span>
              <span className="text-white">{s.suggestedValue}</span>
            </div>

            {/* Reason */}
            <div className="text-xs opacity-50">{s.reason}</div>

            {/* Metadata */}
            <div className="flex items-center gap-3 text-[10px] opacity-30">
              <span>{formatTime(s.createdAt)}</span>
              {s.outcomeSampleCount > 0 && <span>{s.outcomeSampleCount} outcome samples</span>}
              {s.observationContext && <span>{s.observationContext}</span>}
            </div>

            {/* Actions */}
            {s.status === "pending" && (
              <div className="flex gap-2 pt-1">
                <button
                  onClick={() => onResolveAdjustment(s.id, "approve")}
                  className="px-3 py-1 text-xs font-medium rounded-lg border border-green-500/30 bg-green-500/10 text-green-400 hover:bg-green-500/20 cursor-pointer transition-colors"
                >
                  Approve
                </button>
                <button
                  onClick={() => onResolveAdjustment(s.id, "reject")}
                  className="px-3 py-1 text-xs font-medium rounded-lg border border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20 cursor-pointer transition-colors"
                >
                  Reject
                </button>
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}
