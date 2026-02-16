import React, { useState } from "react";
import { toggleRule, deleteRule, guessSensorUnit, type CortexRule, type Device } from "../api";
import { RuleFormModal } from "./RuleFormModal";

type RulesProps = {
  rules: CortexRule[];
  setRules: React.Dispatch<React.SetStateAction<CortexRule[]>>;
  addError: (message: string, source?: string) => void;
  devices: Device[];
};

type Filter = "all" | "enabled" | "disabled";

function conditionSummary(c: CortexRule["condition"]): string[] {
  const pills: string[] = [];
  const unit = guessSensorUnit(c.sensor);
  pills.push(`${c.sensor} ${c.operator} ${c.threshold}${unit}`);
  if (c.duration_seconds && c.duration_seconds > 0) pills.push(`${c.duration_seconds}s hold`);
  if (c.trend) pills.push(`trend: ${c.trend}`);
  if (c.time_of_day) pills.push(`${c.time_of_day.after}\u2013${c.time_of_day.before}`);
  if (c.forecast) pills.push(`${c.forecast} ${c.forecast_threshold ?? ""}${unit}`);
  if (c.baseline_deviation != null) pills.push(`${c.baseline_deviation}\u03C3 deviation`);
  if (c.scope && c.scope !== "self") pills.push(`scope: ${c.scope}`);
  return pills;
}

function actionSummary(a: CortexRule["action"]): string {
  const val = typeof a.value === "boolean" ? (a.value ? "ON" : "OFF") : String(a.value);
  let s = `${a.target} = ${val}`;
  if (a.target_scope && a.target_scope !== "self") s += ` (${a.target_scope})`;
  return s;
}

function ruleCategory(rule: CortexRule): string {
  const c = rule.condition;
  if (c.scope && c.scope !== "self") return "cross-device";
  if (c.baseline_deviation != null) return "baseline";
  if (c.forecast) return "forecast";
  if (c.trend) return "trend";
  return "threshold";
}

const CATEGORY_ORDER = ["threshold", "trend", "forecast", "baseline", "cross-device"] as const;

const CATEGORY_COLORS: Record<string, { badge: string; accent: string }> = {
  threshold:      { badge: "bg-blue-500/20 text-blue-400",     accent: "#3b82f6" },
  trend:          { badge: "bg-purple-500/20 text-purple-400", accent: "#a855f7" },
  forecast:       { badge: "bg-amber-500/20 text-amber-400",   accent: "#f59e0b" },
  baseline:       { badge: "bg-cyan-500/20 text-cyan-400",     accent: "#06b6d4" },
  "cross-device": { badge: "bg-pink-500/20 text-pink-400",     accent: "#ec4899" },
};

function groupByCategory(rules: CortexRule[]): { category: string; rules: CortexRule[] }[] {
  const map = new Map<string, CortexRule[]>();
  for (const rule of rules) {
    const cat = ruleCategory(rule);
    if (!map.has(cat)) map.set(cat, []);
    map.get(cat)!.push(rule);
  }
  return CATEGORY_ORDER
    .filter((cat) => map.has(cat))
    .map((cat) => ({ category: cat, rules: map.get(cat)! }));
}

export function NerveCenterRules({ rules, setRules, addError, devices }: RulesProps): React.ReactElement {
  const [filter, setFilter] = useState<Filter>("all");
  const [toggling, setToggling] = useState<Set<string>>(new Set());
  const [modalOpen, setModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<CortexRule | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const filtered = rules.filter((r) => {
    if (filter === "enabled") return r.enabled;
    if (filter === "disabled") return !r.enabled;
    return true;
  });

  const handleToggle = async (id: string, enabled: boolean) => {
    setToggling((prev) => new Set(prev).add(id));
    try {
      const ok = await toggleRule(id, enabled);
      if (ok) {
        setRules((prev) => prev.map((r) => (r.id === id ? { ...r, enabled } : r)));
      }
    } catch {
      addError(`Failed to toggle rule`, "Cortex");
    } finally {
      setToggling((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const handleDelete = async (id: string) => {
    if (deletingId !== id) {
      setDeletingId(id);
      return;
    }
    try {
      const ok = await deleteRule(id);
      if (ok) {
        setRules((prev) => prev.filter((r) => r.id !== id));
      }
    } catch {
      addError("Failed to delete rule", "Cortex");
    } finally {
      setDeletingId(null);
    }
  };

  const handleEdit = (rule: CortexRule) => {
    setEditingRule(rule);
    setModalOpen(true);
  };

  const handleCreate = () => {
    setEditingRule(null);
    setModalOpen(true);
  };

  const handleSaved = (rule: CortexRule) => {
    if (editingRule) {
      setRules((prev) => prev.map((r) => (r.id === rule.id ? rule : r)));
    } else {
      setRules((prev) => [...prev, rule]);
    }
    setModalOpen(false);
    setEditingRule(null);
  };

  return (
    <div className="space-y-4">
      {/* Filter bar + Create button */}
      <div className="flex items-center justify-between gap-2">
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
        <button
          onClick={handleCreate}
          className="px-3 py-1 text-xs rounded-lg border border-teal-500/30 bg-teal-500/10 text-teal-400 hover:bg-teal-500/20 transition-colors cursor-pointer"
        >
          + Create Rule
        </button>
      </div>

      {/* Rule card grid grouped by category */}
      {filtered.length === 0 ? (
        <div className="text-sm opacity-50 text-center py-8">No rules match this filter</div>
      ) : (
        <div className="space-y-6">
          {groupByCategory(filtered).map(({ category, rules: groupRules }) => {
            const colors = CATEGORY_COLORS[category] || CATEGORY_COLORS.threshold;
            return (
              <section key={category}>
                {/* Category heading */}
                <div className="flex items-center gap-2 mb-3">
                  <span
                    className="w-1 h-4 rounded-full"
                    style={{ backgroundColor: colors.accent }}
                  />
                  <h3 className="text-xs font-semibold uppercase tracking-wider opacity-60">
                    {category}
                  </h3>
                  <span className="text-[10px] opacity-30">{groupRules.length}</span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                  {groupRules.map((rule) => {
                    const isToggling = toggling.has(rule.id);
                    const isConfirmingDelete = deletingId === rule.id;

                    return (
                      <div
                        key={rule.id}
                        className={`rounded-xl border-l-[3px] border backdrop-blur-[6px] p-4 transition-colors flex flex-col ${
                          rule.enabled
                            ? "border-panel-border bg-panel/30"
                            : "border-panel-border/50 bg-panel/10 opacity-60"
                        }`}
                        style={{ borderLeftColor: colors.accent }}
                      >
                        {/* Header: badges left, actions right */}
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            {rule.modified && (
                              <span className="px-1.5 py-0.5 text-[10px] font-medium rounded border border-amber-500/30 bg-amber-500/20 text-amber-400">
                                modified
                              </span>
                            )}
                            {rule.source === "user" && (
                              <span className="px-1.5 py-0.5 text-[10px] font-medium rounded border border-teal-500/30 bg-teal-500/20 text-teal-400">
                                custom
                              </span>
                            )}
                            {rule.source === "generated" && (
                              <span className="px-1.5 py-0.5 text-[10px] font-medium rounded border border-violet-500/30 bg-violet-500/20 text-violet-400">
                                generated
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-1.5">
                            {/* Edit button */}
                            <button
                              onClick={() => handleEdit(rule)}
                              className="p-1 rounded text-white/30 hover:text-white/70 hover:bg-white/5 transition-colors cursor-pointer"
                              aria-label={`Edit ${rule.name}`}
                            >
                              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                              </svg>
                            </button>
                            {/* Delete button */}
                            <button
                              onClick={() => handleDelete(rule.id)}
                              className={`p-1 rounded transition-colors cursor-pointer ${
                                isConfirmingDelete
                                  ? "text-red-400 bg-red-500/10"
                                  : "text-white/30 hover:text-red-400 hover:bg-white/5"
                              }`}
                              onBlur={() => setDeletingId(null)}
                              aria-label={isConfirmingDelete ? "Confirm delete" : `Delete ${rule.name}`}
                            >
                              {isConfirmingDelete ? (
                                <span className="text-[10px] font-medium px-0.5">Confirm?</span>
                              ) : (
                                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                </svg>
                              )}
                            </button>
                            {/* Toggle */}
                            <button
                              onClick={() => handleToggle(rule.id, !rule.enabled)}
                              disabled={isToggling}
                              className={`relative flex-shrink-0 w-9 h-5 rounded-full transition-colors cursor-pointer disabled:cursor-not-allowed ${
                                rule.enabled ? "bg-teal-500" : "bg-white/20"
                              }`}
                              aria-label={`${rule.enabled ? "Disable" : "Enable"} ${rule.name}`}
                            >
                              <span
                                className={`absolute left-0 top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                                  rule.enabled ? "translate-x-[18px]" : "translate-x-0.5"
                                }`}
                              />
                            </button>
                          </div>
                        </div>

                        {/* Title */}
                        <div className="text-sm font-semibold mt-2">{rule.name}</div>

                        {/* Description */}
                        <div className="text-xs opacity-50 mt-0.5">{rule.description}</div>

                        {/* Divider */}
                        <div className="border-t border-white/5 my-3" />

                        {/* Condition pills */}
                        <div className="flex flex-wrap gap-1.5">
                          {conditionSummary(rule.condition).map((pill, i) => (
                            <span key={i} className="px-2 py-0.5 text-[11px] rounded-md bg-white/5 border border-white/10 text-white/70">
                              {pill}
                            </span>
                          ))}
                        </div>

                        {/* Action */}
                        <div className="text-xs opacity-50 mt-2">
                          Action: <span className="text-white/70">{actionSummary(rule.action)}</span>
                        </div>

                        {/* Reason */}
                        {rule.action.reason && (
                          <div className="text-xs opacity-40 mt-1">{rule.action.reason}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {/* Rule form modal */}
      {modalOpen && (
        <RuleFormModal
          rule={editingRule}
          onClose={() => { setModalOpen(false); setEditingRule(null); }}
          onSaved={handleSaved}
          addError={addError}
          devices={devices}
        />
      )}
    </div>
  );
}
