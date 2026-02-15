import React, { useState } from "react";
import { toggleRule, type CortexRule } from "../api";

type RulesProps = {
  rules: CortexRule[];
  setRules: React.Dispatch<React.SetStateAction<CortexRule[]>>;
  addError: (message: string, source?: string) => void;
};

type Filter = "all" | "enabled" | "disabled";

function conditionSummary(c: CortexRule["condition"]): string[] {
  const pills: string[] = [];
  const unit = c.sensor.startsWith("temp") ? "\u00B0C" : "%";
  pills.push(`${c.sensor} ${c.operator} ${c.threshold}${unit}`);
  if (c.duration_seconds > 0) pills.push(`${c.duration_seconds}s hold`);
  if (c.trend) pills.push(`trend: ${c.trend}`);
  if (c.time_of_day) pills.push(`${c.time_of_day.after}\u2013${c.time_of_day.before}`);
  if (c.forecast) pills.push(`${c.forecast} ${c.forecast_threshold ?? ""}${unit}`);
  if (c.baseline_deviation != null) pills.push(`${c.baseline_deviation}\u03C3 deviation`);
  if (c.scope !== "self") pills.push(`scope: ${c.scope}`);
  return pills;
}

function actionSummary(a: CortexRule["action"]): string {
  const val = typeof a.value === "boolean" ? (a.value ? "ON" : "OFF") : String(a.value);
  let s = `${a.target} = ${val}`;
  if (a.target_scope !== "self") s += ` (${a.target_scope})`;
  return s;
}

function ruleCategory(rule: CortexRule): string {
  const c = rule.condition;
  if (c.scope !== "self") return "cross-device";
  if (c.baseline_deviation != null) return "baseline";
  if (c.forecast) return "forecast";
  if (c.trend) return "trend";
  return "threshold";
}

const CATEGORY_COLORS: Record<string, string> = {
  threshold: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  trend: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  forecast: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  baseline: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
  "cross-device": "bg-pink-500/20 text-pink-400 border-pink-500/30",
};

export function NerveCenterRules({ rules, setRules, addError }: RulesProps): React.ReactElement {
  const [filter, setFilter] = useState<Filter>("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [toggling, setToggling] = useState<Set<string>>(new Set());

  const filtered = rules.filter((r) => {
    if (filter === "enabled") return r.enabled;
    if (filter === "disabled") return !r.enabled;
    return true;
  });

  const handleToggle = async (name: string, enabled: boolean) => {
    setToggling((prev) => new Set(prev).add(name));
    try {
      const ok = await toggleRule(name, enabled);
      if (ok) {
        // Optimistic update — WS broadcast will confirm
        setRules((prev) => prev.map((r) => (r.name === name ? { ...r, enabled } : r)));
      }
    } catch {
      addError(`Failed to toggle rule "${name}"`, "Cortex");
    } finally {
      setToggling((prev) => {
        const next = new Set(prev);
        next.delete(name);
        return next;
      });
    }
  };

  const toggleExpand = (name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  return (
    <div className="space-y-3">
      {/* Filter bar */}
      <div className="flex gap-2 text-xs">
        {(["all", "enabled", "disabled"] as Filter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-2.5 py-1 rounded-lg border transition-colors cursor-pointer ${
              filter === f
                ? "border-white/20 bg-white/10 text-white"
                : "border-panel-border text-white/40 hover:text-white/60"
            }`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
            {f === "all" && ` (${rules.length})`}
            {f === "enabled" && ` (${rules.filter((r) => r.enabled).length})`}
            {f === "disabled" && ` (${rules.filter((r) => !r.enabled).length})`}
          </button>
        ))}
      </div>

      {/* Rule list */}
      {filtered.length === 0 ? (
        <div className="text-sm opacity-50 text-center py-8">No rules match this filter</div>
      ) : (
        filtered.map((rule) => {
          const cat = ruleCategory(rule);
          const isExpanded = expanded.has(rule.name);
          const isToggling = toggling.has(rule.name);

          return (
            <div
              key={rule.name}
              className={`rounded-xl border backdrop-blur-[6px] p-4 transition-colors ${
                rule.enabled
                  ? "border-panel-border bg-panel/30"
                  : "border-panel-border/50 bg-panel/10 opacity-60"
              }`}
            >
              {/* Header row */}
              <div className="flex items-center gap-3">
                {/* Toggle switch */}
                <button
                  onClick={() => handleToggle(rule.name, !rule.enabled)}
                  disabled={isToggling}
                  className={`relative flex-shrink-0 w-9 h-5 rounded-full transition-colors cursor-pointer disabled:cursor-not-allowed ${
                    rule.enabled ? "bg-teal-500" : "bg-white/20"
                  }`}
                  aria-label={`${rule.enabled ? "Disable" : "Enable"} ${rule.name}`}
                >
                  <span
                    className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                      rule.enabled ? "translate-x-[18px]" : "translate-x-0.5"
                    }`}
                  />
                </button>

                {/* Name + category badge */}
                <button
                  onClick={() => toggleExpand(rule.name)}
                  className="flex-1 min-w-0 flex items-center gap-2 text-left cursor-pointer"
                >
                  <span className="text-sm font-medium truncate">{rule.name}</span>
                  <span className={`flex-shrink-0 px-1.5 py-0.5 text-[10px] font-medium rounded border ${CATEGORY_COLORS[cat] || ""}`}>
                    {cat}
                  </span>
                  {rule.modified && (
                    <span className="flex-shrink-0 px-1.5 py-0.5 text-[10px] font-medium rounded border border-amber-500/30 bg-amber-500/20 text-amber-400">
                      modified
                    </span>
                  )}
                  {/* Expand indicator */}
                  <svg
                    className={`flex-shrink-0 w-3.5 h-3.5 opacity-30 transition-transform ${isExpanded ? "rotate-180" : ""}`}
                    fill="none" stroke="currentColor" viewBox="0 0 24 24"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
              </div>

              {/* Description */}
              <div className="text-xs opacity-40 mt-1 ml-12">{rule.description}</div>

              {/* Expanded detail */}
              {isExpanded && (
                <div className="mt-3 ml-12 space-y-2">
                  {/* Condition pills */}
                  <div className="flex flex-wrap gap-1.5">
                    {conditionSummary(rule.condition).map((pill, i) => (
                      <span key={i} className="px-2 py-0.5 text-[11px] rounded-md bg-white/5 border border-white/10 text-white/70">
                        {pill}
                      </span>
                    ))}
                  </div>

                  {/* Action */}
                  <div className="text-xs opacity-50">
                    Action: <span className="text-white/70">{actionSummary(rule.action)}</span>
                  </div>

                  {/* Reason */}
                  <div className="text-xs opacity-40">{rule.action.reason}</div>
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
