import React, { useState, useEffect, useRef, useMemo } from "react";
import { createGoal, updateGoal, type GrowGoal, type Device } from "../api";

type GoalFormModalProps = {
  profileId: string;
  goal: GrowGoal | null;
  onClose: () => void;
  onSaved: (goal: GrowGoal) => void;
  addError: (message: string, source?: string) => void;
  devices: Device[];
};

const PHASES = ["seedling", "veg", "flower", "late_flower", "dry", "cure"] as const;
const METRIC_TYPES = ["sensor", "derived", "relay_schedule"] as const;
const DERIVED_METRICS = ["vpd", "dli", "dryback_rate"] as const;

const inputClass = "w-full px-2.5 py-1.5 text-sm bg-white/5 border border-white/10 rounded-lg text-white placeholder:text-white/20 focus:outline-none focus:border-white/25";
const selectClass = "w-full px-2.5 py-1.5 text-sm bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:border-white/25 appearance-none";
const labelClass = "block text-[11px] uppercase tracking-wider text-white/40 mb-1";

export function GoalFormModal({ profileId, goal, onClose, onSaved, addError, devices }: GoalFormModalProps): React.ReactElement {
  const isEdit = goal !== null;
  const backdropRef = useRef<HTMLDivElement>(null);

  // Collect available sensor IDs from devices
  const sensorIds = useMemo(() => {
    const ids = new Set<string>();
    for (const d of devices) {
      for (const s of d.capabilities.sensors) ids.add(s.id);
    }
    return [...ids].sort();
  }, [devices]);

  const allMetrics = useMemo(() => {
    return [...sensorIds, ...DERIVED_METRICS];
  }, [sensorIds]);

  const [metric, setMetric] = useState(goal?.metric ?? allMetrics[0] ?? "");
  const [metricType, setMetricType] = useState<string>(goal?.metricType ?? "sensor");
  const [phase, setPhase] = useState(goal?.phase ?? "");
  const [rangeMin, setRangeMin] = useState(goal?.rangeMin != null ? String(goal.rangeMin) : "");
  const [rangeMax, setRangeMax] = useState(goal?.rangeMax != null ? String(goal.rangeMax) : "");
  const [tolerance, setTolerance] = useState(String(goal?.tolerance ?? 0));
  const [priority, setPriority] = useState(String(goal?.priority ?? 1));
  const [twOnHour, setTwOnHour] = useState(goal?.timeWindow ? String(goal.timeWindow.onHour) : "");
  const [twOffHour, setTwOffHour] = useState(goal?.timeWindow ? String(goal.timeWindow.offHour) : "");
  const [saving, setSaving] = useState(false);

  // Auto-set metricType based on metric
  useEffect(() => {
    if (DERIVED_METRICS.includes(metric as typeof DERIVED_METRICS[number])) {
      setMetricType("derived");
    } else if (sensorIds.includes(metric)) {
      setMetricType("sensor");
    }
  }, [metric, sensorIds]);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handleEsc);
    return () => document.removeEventListener("keydown", handleEsc);
  }, [onClose]);

  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === backdropRef.current) onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!metric.trim()) return;

    const body = {
      metric: metric.trim(),
      metricType,
      phase: phase || undefined,
      rangeMin: rangeMin ? parseFloat(rangeMin) : undefined,
      rangeMax: rangeMax ? parseFloat(rangeMax) : undefined,
      tolerance: parseFloat(tolerance) || 0,
      priority: parseFloat(priority) || 1,
      timeWindow: twOnHour && twOffHour
        ? { onHour: parseInt(twOnHour), offHour: parseInt(twOffHour) }
        : undefined,
    };

    setSaving(true);
    try {
      if (isEdit) {
        const result = await updateGoal(goal.id, body);
        if (result.ok && result.goal) {
          onSaved(result.goal);
        } else {
          addError(result.error || "Failed to update goal", "Cortex");
        }
      } else {
        const result = await createGoal(profileId, body);
        if (result.ok && result.goal) {
          onSaved(result.goal);
        } else {
          addError(result.error || "Failed to create goal", "Cortex");
        }
      }
    } catch {
      addError("Failed to save goal", "Cortex");
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
        className="w-full max-w-lg mx-4 rounded-2xl border border-white/10 bg-[#1a1a2e]/95 backdrop-blur-xl shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/5">
          <h2 className="text-lg font-semibold">{isEdit ? "Edit Goal" : "Add Goal"}</h2>
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

        <div className="px-6 py-5 space-y-4">
          {/* Metric + Type */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Metric</label>
              {allMetrics.length > 0 ? (
                <select className={selectClass} value={metric} onChange={(e) => setMetric(e.target.value)}>
                  {allMetrics.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              ) : (
                <input className={inputClass} value={metric} onChange={(e) => setMetric(e.target.value)} placeholder="sensor_id" required />
              )}
            </div>
            <div>
              <label className={labelClass}>Metric Type</label>
              <select className={selectClass} value={metricType} onChange={(e) => setMetricType(e.target.value)}>
                {METRIC_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
          </div>

          {/* Phase */}
          <div>
            <label className={labelClass}>Phase (optional)</label>
            <select className={selectClass} value={phase} onChange={(e) => setPhase(e.target.value)}>
              <option value="">All phases</option>
              {PHASES.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>

          {/* Range */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Range Min</label>
              <input className={inputClass} type="number" step="any" value={rangeMin} onChange={(e) => setRangeMin(e.target.value)} placeholder="--" />
            </div>
            <div>
              <label className={labelClass}>Range Max</label>
              <input className={inputClass} type="number" step="any" value={rangeMax} onChange={(e) => setRangeMax(e.target.value)} placeholder="--" />
            </div>
          </div>

          {/* Tolerance + Priority */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Tolerance</label>
              <input className={inputClass} type="number" step="any" value={tolerance} onChange={(e) => setTolerance(e.target.value)} />
            </div>
            <div>
              <label className={labelClass}>Priority</label>
              <input className={inputClass} type="number" step="any" value={priority} onChange={(e) => setPriority(e.target.value)} />
            </div>
          </div>

          {/* Time Window */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Time Window On (hour)</label>
              <input className={inputClass} type="number" min="0" max="23" value={twOnHour} onChange={(e) => setTwOnHour(e.target.value)} placeholder="--" />
            </div>
            <div>
              <label className={labelClass}>Time Window Off (hour)</label>
              <input className={inputClass} type="number" min="0" max="23" value={twOffHour} onChange={(e) => setTwOffHour(e.target.value)} placeholder="--" />
            </div>
          </div>
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
            disabled={saving || !metric.trim()}
            className="px-4 py-1.5 text-sm rounded-lg bg-teal-500/80 text-white hover:bg-teal-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors cursor-pointer"
          >
            {saving ? "Saving..." : isEdit ? "Save Changes" : "Add Goal"}
          </button>
        </div>
      </form>
    </div>
  );
}