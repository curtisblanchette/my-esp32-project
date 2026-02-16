import React, { useState, useEffect, useRef, useMemo } from "react";
import { createRule, updateRule, type CortexRule, type Device } from "../api";

type RuleFormModalProps = {
  rule: CortexRule | null; // null = create mode
  onClose: () => void;
  onSaved: (rule: CortexRule) => void;
  addError: (message: string, source?: string) => void;
  devices: Device[];
};

const OPERATORS = [">", "<", ">=", "<=", "==", "!="];
const TRENDS = ["rising", "falling", "stable"];
const FORECASTS = ["will_exceed", "will_drop_below"];
const SCOPES = ["self", "any", "all"];

type FormState = {
  name: string;
  description: string;
  enabled: boolean;
  // Condition
  sensor: string;
  operator: string;
  threshold: string;
  duration_seconds: string;
  trend: string;
  trend_window_minutes: string;
  time_after: string;
  time_before: string;
  forecast: string;
  forecast_threshold: string;
  forecast_within_minutes: string;
  baseline_deviation: string;
  scope: string;
  // Action
  target: string;
  action_type: string;
  value: string;
  reason: string;
  target_scope: string;
};

function initForm(rule: CortexRule | null, defaultSensor: string, defaultTarget: string): FormState {
  if (!rule) {
    return {
      name: "",
      description: "",
      enabled: true,
      sensor: defaultSensor,
      operator: ">",
      threshold: "",
      duration_seconds: "0",
      trend: "",
      trend_window_minutes: "30",
      time_after: "",
      time_before: "",
      forecast: "",
      forecast_threshold: "",
      forecast_within_minutes: "15",
      baseline_deviation: "",
      scope: "self",
      target: defaultTarget,
      action_type: "set",
      value: "true",
      reason: "",
      target_scope: "self",
    };
  }

  const c = rule.condition;
  const a = rule.action;
  return {
    name: rule.name,
    description: rule.description,
    enabled: rule.enabled,
    sensor: c.sensor,
    operator: c.operator,
    threshold: String(c.threshold),
    duration_seconds: String(c.duration_seconds ?? 0),
    trend: c.trend ?? "",
    trend_window_minutes: String(c.trend_window_minutes ?? 30),
    time_after: c.time_of_day?.after ?? "",
    time_before: c.time_of_day?.before ?? "",
    forecast: c.forecast ?? "",
    forecast_threshold: c.forecast_threshold != null ? String(c.forecast_threshold) : "",
    forecast_within_minutes: String(c.forecast_within_minutes ?? 15),
    baseline_deviation: c.baseline_deviation != null ? String(c.baseline_deviation) : "",
    scope: c.scope ?? "self",
    target: a.target,
    action_type: a.action,
    value: String(a.value),
    reason: a.reason,
    target_scope: a.target_scope ?? "self",
  };
}

function formToPayload(form: FormState) {
  const condition: Record<string, unknown> = {
    sensor: form.sensor,
    operator: form.operator,
    threshold: parseFloat(form.threshold) || 0,
  };
  if (parseInt(form.duration_seconds)) condition.duration_seconds = parseInt(form.duration_seconds);
  if (form.trend) condition.trend = form.trend;
  if (form.trend && form.trend_window_minutes) condition.trend_window_minutes = parseInt(form.trend_window_minutes);
  if (form.time_after && form.time_before) {
    condition.time_of_day = { after: form.time_after, before: form.time_before };
  }
  if (form.forecast) condition.forecast = form.forecast;
  if (form.forecast && form.forecast_threshold) condition.forecast_threshold = parseFloat(form.forecast_threshold);
  if (form.forecast && form.forecast_within_minutes) condition.forecast_within_minutes = parseFloat(form.forecast_within_minutes);
  if (form.baseline_deviation) condition.baseline_deviation = parseFloat(form.baseline_deviation);
  if (form.scope !== "self") condition.scope = form.scope;

  // Parse value: "true"/"false" → boolean, number strings → number
  let parsedValue: boolean | number | string = form.value;
  if (form.value === "true") parsedValue = true;
  else if (form.value === "false") parsedValue = false;
  else if (!isNaN(Number(form.value)) && form.value.trim() !== "") parsedValue = Number(form.value);

  const action: Record<string, unknown> = {
    target: form.target,
    action: form.action_type,
    value: parsedValue,
    reason: form.reason,
  };
  if (form.target_scope !== "self") action.target_scope = form.target_scope;

  return {
    name: form.name,
    description: form.description,
    condition: condition as CortexRule["condition"],
    action: action as CortexRule["action"],
    enabled: form.enabled,
  };
}

const inputClass = "w-full px-2.5 py-1.5 text-sm bg-white/5 border border-white/10 rounded-lg text-white placeholder:text-white/20 focus:outline-none focus:border-white/25";
const selectClass = "w-full px-2.5 py-1.5 text-sm bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:border-white/25 appearance-none";
const labelClass = "block text-[11px] uppercase tracking-wider text-white/40 mb-1";

export function RuleFormModal({ rule, onClose, onSaved, addError, devices }: RuleFormModalProps): React.ReactElement {
  const sensors = useMemo(() => {
    const ids = new Set<string>();
    for (const d of devices) for (const s of d.capabilities.sensors) ids.add(s.id);
    return [...ids].sort();
  }, [devices]);

  const targets = useMemo(() => {
    const ids = new Set<string>(["notification_center"]);
    for (const d of devices) for (const a of d.capabilities.actuators) ids.add(a.id);
    return [...ids].sort();
  }, [devices]);

  const [form, setForm] = useState<FormState>(() => initForm(rule, sensors[0] || "", targets[0] || ""));
  const [saving, setSaving] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(() => {
    if (!rule) return false;
    const c = rule.condition;
    return !!(c.trend || c.time_of_day || c.forecast || c.baseline_deviation != null || (c.scope && c.scope !== "self"));
  });
  const backdropRef = useRef<HTMLDivElement>(null);

  const isEdit = rule !== null;

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handleEsc);
    return () => document.removeEventListener("keydown", handleEsc);
  }, [onClose]);

  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === backdropRef.current) onClose();
  };

  const set = (field: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    setForm((prev) => ({ ...prev, [field]: e.target.value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim() || !form.reason.trim()) return;

    setSaving(true);
    try {
      const payload = formToPayload(form);
      const result = isEdit
        ? await updateRule(rule.id, payload)
        : await createRule(payload);

      if (result.ok && result.rule) {
        onSaved(result.rule);
      } else {
        addError(result.error || "Failed to save rule", "Cortex");
      }
    } catch {
      addError("Failed to save rule", "Cortex");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      ref={backdropRef}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
    >
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-2xl max-h-[85vh] overflow-y-auto mx-4 rounded-2xl border border-white/10 bg-[#1a1a2e]/95 backdrop-blur-xl shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/5">
          <h2 className="text-lg font-semibold">{isEdit ? "Edit Rule" : "Create Rule"}</h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-lg text-white/40 hover:text-white/70 hover:bg-white/5 transition-colors cursor-pointer"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {/* Basic */}
          <section className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={labelClass}>Name</label>
                <input className={inputClass} value={form.name} onChange={set("name")} placeholder="my_rule_name" required />
              </div>
              <div>
                <label className={labelClass}>Enabled</label>
                <button
                  type="button"
                  onClick={() => setForm((prev) => ({ ...prev, enabled: !prev.enabled }))}
                  className={`relative w-9 h-5 rounded-full transition-colors cursor-pointer mt-1 ${
                    form.enabled ? "bg-teal-500" : "bg-white/20"
                  }`}
                >
                  <span className={`absolute left-0 top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                    form.enabled ? "translate-x-[18px]" : "translate-x-0.5"
                  }`} />
                </button>
              </div>
            </div>
            <div>
              <label className={labelClass}>Description</label>
              <input className={inputClass} value={form.description} onChange={set("description")} placeholder="What this rule does..." required />
            </div>
          </section>

          {/* Condition */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-3">Condition</h3>
            <div className="grid grid-cols-4 gap-3">
              <div>
                <label className={labelClass}>Sensor</label>
                {sensors.length > 0 ? (
                  <select className={selectClass} value={form.sensor} onChange={set("sensor")}>
                    {sensors.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                ) : (
                  <input className={inputClass} value={form.sensor} onChange={set("sensor")} placeholder="sensor_id" />
                )}
              </div>
              <div>
                <label className={labelClass}>Operator</label>
                <select className={selectClass} value={form.operator} onChange={set("operator")}>
                  {OPERATORS.map((op) => <option key={op} value={op}>{op}</option>)}
                </select>
              </div>
              <div>
                <label className={labelClass}>Threshold</label>
                <input className={inputClass} type="number" step="any" value={form.threshold} onChange={set("threshold")} required />
              </div>
              <div>
                <label className={labelClass}>Duration (s)</label>
                <input className={inputClass} type="number" value={form.duration_seconds} onChange={set("duration_seconds")} />
              </div>
            </div>
          </section>

          {/* Advanced (collapsible) */}
          <section>
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-white/50 hover:text-white/70 transition-colors cursor-pointer"
            >
              <svg className={`w-3 h-3 transition-transform ${showAdvanced ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
              Advanced Conditions
            </button>

            {showAdvanced && (
              <div className="mt-3 space-y-3">
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className={labelClass}>Trend</label>
                    <select className={selectClass} value={form.trend} onChange={set("trend")}>
                      <option value="">None</option>
                      {TRENDS.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className={labelClass}>Time After</label>
                    <input className={inputClass} type="time" value={form.time_after} onChange={set("time_after")} />
                  </div>
                  <div>
                    <label className={labelClass}>Time Before</label>
                    <input className={inputClass} type="time" value={form.time_before} onChange={set("time_before")} />
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className={labelClass}>Forecast</label>
                    <select className={selectClass} value={form.forecast} onChange={set("forecast")}>
                      <option value="">None</option>
                      {FORECASTS.map((f) => <option key={f} value={f}>{f}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className={labelClass}>Forecast Threshold</label>
                    <input className={inputClass} type="number" step="any" value={form.forecast_threshold} onChange={set("forecast_threshold")} />
                  </div>
                  <div>
                    <label className={labelClass}>Forecast Within (min)</label>
                    <input className={inputClass} type="number" value={form.forecast_within_minutes} onChange={set("forecast_within_minutes")} />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className={labelClass}>Baseline Deviation (&sigma;)</label>
                    <input className={inputClass} type="number" step="any" value={form.baseline_deviation} onChange={set("baseline_deviation")} placeholder="e.g. 2.0" />
                  </div>
                  <div>
                    <label className={labelClass}>Scope</label>
                    <select className={selectClass} value={form.scope} onChange={set("scope")}>
                      {SCOPES.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </div>
                </div>
              </div>
            )}
          </section>

          {/* Action */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-3">Action</h3>
            <div className="grid grid-cols-4 gap-3">
              <div>
                <label className={labelClass}>Target</label>
                <select className={selectClass} value={form.target} onChange={set("target")}>
                  {targets.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div>
                <label className={labelClass}>Action</label>
                <input className={inputClass} value={form.action_type} onChange={set("action_type")} placeholder="set" />
              </div>
              <div>
                <label className={labelClass}>Value</label>
                <select className={selectClass} value={form.value} onChange={set("value")}>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              </div>
              <div>
                <label className={labelClass}>Target Scope</label>
                <select className={selectClass} value={form.target_scope} onChange={set("target_scope")}>
                  {SCOPES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
            </div>
            <div className="mt-3">
              <label className={labelClass}>Reason</label>
              <textarea
                className={`${inputClass} resize-none`}
                rows={2}
                value={form.reason}
                onChange={set("reason")}
                placeholder="Why this action should be taken..."
                required
              />
            </div>
          </section>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-white/5">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 text-sm rounded-lg border border-white/10 text-white/50 hover:text-white/70 hover:bg-white/5 transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving || !form.name.trim() || !form.reason.trim()}
            className="px-4 py-1.5 text-sm rounded-lg bg-teal-500/80 text-white hover:bg-teal-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors cursor-pointer"
          >
            {saving ? "Saving..." : isEdit ? "Save Changes" : "Create Rule"}
          </button>
        </div>
      </form>
    </div>
  );
}
