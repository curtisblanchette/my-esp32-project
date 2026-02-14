import React, { useState } from "react";
import { OBSERVATION_CATEGORIES, type ObservationCategory, type Device } from "../api";

type ObservationFormProps = {
  devices: Device[];
  onSubmit: (obs: { deviceId: string; category: ObservationCategory; notes?: string }) => Promise<void>;
  onCancel: () => void;
};

export function ObservationForm({ devices, onSubmit, onCancel }: ObservationFormProps): React.ReactElement {
  const [selectedDevice, setSelectedDevice] = useState(devices[0]?.id ?? "");
  const [selectedCategory, setSelectedCategory] = useState<ObservationCategory | null>(null);
  const [notes, setNotes] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const canSubmit = selectedCategory !== null && selectedDevice !== "" && !isSubmitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || !selectedCategory) return;

    setIsSubmitting(true);
    try {
      await onSubmit({
        deviceId: selectedDevice,
        category: selectedCategory,
        notes: notes.trim() || undefined,
      });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3 p-3 rounded-xl border border-panel-border bg-panel/80 backdrop-blur-[10px]">
      {/* Device selector */}
      {devices.length > 1 && (
        <select
          value={selectedDevice}
          onChange={(e) => setSelectedDevice(e.target.value)}
          className="w-full bg-white/5 border border-white/10 rounded-lg text-sm px-3 py-2 focus:outline-none focus:border-amber-500/50"
        >
          {devices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name || d.id}
            </option>
          ))}
        </select>
      )}

      {/* Category chips */}
      <div className="flex flex-wrap gap-1.5">
        {OBSERVATION_CATEGORIES.map((cat) => (
          <button
            key={cat.key}
            type="button"
            onClick={() => setSelectedCategory(cat.key)}
            className={`px-2.5 py-1.5 text-xs rounded-full border transition-colors cursor-pointer ${
              selectedCategory === cat.key
                ? "bg-amber-500/20 text-amber-400 border-amber-500/30"
                : "bg-white/5 text-white/60 border-white/10 hover:bg-white/10 hover:text-white/80"
            }`}
          >
            {cat.label}
          </button>
        ))}
      </div>

      {/* Notes textarea */}
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder={selectedCategory === "general" ? "Describe what you observed..." : "Add details (optional)..."}
        rows={2}
        className="w-full bg-white/5 border border-white/10 rounded-lg text-sm px-3 py-2 resize-none focus:outline-none focus:border-amber-500/50 placeholder:text-white/30"
      />

      {/* Actions */}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1.5 text-xs rounded-lg border border-white/10 text-white/60 hover:bg-white/10 transition-colors cursor-pointer"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={!canSubmit}
          className="px-3 py-1.5 text-xs rounded-lg border border-amber-500/30 bg-amber-500/20 text-amber-400 hover:bg-amber-500/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
        >
          {isSubmitting ? "Logging..." : "Log Observation"}
        </button>
      </div>
    </form>
  );
}
