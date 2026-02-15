import React, { useState } from "react";
import type { CortexStatus, CortexRule, RuleSuggestion } from "../api";

type OverviewProps = {
  status: CortexStatus | null;
  rules: CortexRule[];
  suggestions: RuleSuggestion[];
  onRunAdvisor: () => Promise<void>;
  onTabChange: (tab: "overview" | "rules" | "suggestions" | "baselines") => void;
};

function formatTime(ts: number): string {
  const date = new Date(ts);
  const now = Date.now();
  const diff = now - ts;

  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function StatCard({ label, value, sub, color }: { label: string; value: string | number; sub?: string; color?: string }): React.ReactElement {
  return (
    <div className="rounded-xl border border-panel-border bg-panel/30 backdrop-blur-[6px] p-4 flex-1 min-w-[140px]">
      <div className="text-xs opacity-50 mb-1">{label}</div>
      <div className={`text-2xl font-semibold ${color || "text-white"}`}>{value}</div>
      {sub && <div className="text-xs opacity-40 mt-0.5">{sub}</div>}
    </div>
  );
}

export function NerveCenterOverview({ status, rules, suggestions, onRunAdvisor, onTabChange }: OverviewProps): React.ReactElement {
  const [advisorRunning, setAdvisorRunning] = useState(false);

  const enabledRules = rules.filter((r) => r.enabled).length;
  const modifiedRules = rules.filter((r) => r.modified).length;
  const pendingSuggestions = suggestions.filter((s) => s.status === "pending").length;

  const avgEff = status?.outcomes.avgEffectiveness ?? 0;
  const effColor = avgEff > 0.5 ? "text-green-400" : avgEff > 0 ? "text-yellow-400" : "text-red-400";

  return (
    <div className="space-y-5">
      {/* Stat cards */}
      <div className="flex flex-wrap gap-3">
        <StatCard
          label="Effectiveness"
          value={status ? `${(avgEff * 100).toFixed(0)}%` : "--"}
          sub={status ? `${status.outcomes.total} outcomes, ${(status.outcomes.successRate * 100).toFixed(0)}% success` : undefined}
          color={effColor}
        />
        <StatCard
          label="Baselines"
          value={status?.baselines.total ?? "--"}
          sub={status ? `${status.baselines.sensorsTracked} sensors tracked` : undefined}
        />
        <StatCard
          label="Rules Active"
          value={`${enabledRules}/${rules.length}`}
          sub={modifiedRules > 0 ? `${modifiedRules} modified by advisor` : undefined}
        />
      </div>

      {/* Suggestions + Advisor */}
      <div className="rounded-xl border border-panel-border bg-panel/30 backdrop-blur-[6px] p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm font-medium">Rule Advisor</div>
            <div className="text-xs opacity-40 mt-0.5">
              {status?.lastAdvisorRun
                ? `Last run: ${formatTime(status.lastAdvisorRun)}`
                : "Never run"}
            </div>
          </div>
          <button
            onClick={async () => {
              setAdvisorRunning(true);
              try { await onRunAdvisor(); } finally { setAdvisorRunning(false); }
            }}
            disabled={advisorRunning}
            className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-lg border border-teal-500/30 bg-teal-500/10 text-teal-400 hover:bg-teal-500/20 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer transition-colors"
          >
            {advisorRunning ? (
              <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            ) : (
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
            )}
            {advisorRunning ? "Running..." : "Run Now"}
          </button>
        </div>

        {/* Suggestion summary */}
        <div className="flex items-center gap-3 text-xs">
          <span className="opacity-50">Suggestions:</span>
          <span className="px-1.5 py-0.5 rounded bg-teal-500/20 text-teal-400">{pendingSuggestions} pending</span>
          <span className="px-1.5 py-0.5 rounded bg-green-500/20 text-green-400">{status?.suggestions.applied ?? 0} applied</span>
          <span className="px-1.5 py-0.5 rounded bg-red-500/20 text-red-400">{status?.suggestions.rejected ?? 0} rejected</span>
        </div>

        {pendingSuggestions > 0 && (
          <button
            onClick={() => onTabChange("suggestions")}
            className="text-xs text-teal-400 hover:text-teal-300 cursor-pointer transition-colors"
          >
            View {pendingSuggestions} pending suggestion{pendingSuggestions !== 1 ? "s" : ""}
          </button>
        )}
      </div>
    </div>
  );
}
