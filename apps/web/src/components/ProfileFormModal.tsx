import React, { useState, useEffect, useRef } from "react";
import { createProfile, updateProfile, type GrowProfile } from "../api";

type ProfileFormModalProps = {
  profile: GrowProfile | null;
  onClose: () => void;
  onSaved: (profile: GrowProfile) => void;
  addError: (message: string, source?: string) => void;
};

const STRATEGIES = ["precision", "balanced", "efficiency"] as const;
const PHASES = ["seedling", "veg", "flower", "late_flower", "dry", "cure"] as const;

const inputClass = "w-full px-2.5 py-1.5 text-sm bg-white/5 border border-white/10 rounded-lg text-white placeholder:text-white/20 focus:outline-none focus:border-white/25";
const selectClass = "w-full px-2.5 py-1.5 text-sm bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:border-white/25 appearance-none";
const labelClass = "block text-[11px] uppercase tracking-wider text-white/40 mb-1";

export function ProfileFormModal({ profile, onClose, onSaved, addError }: ProfileFormModalProps): React.ReactElement {
  const isEdit = profile !== null;
  const backdropRef = useRef<HTMLDivElement>(null);

  const [name, setName] = useState(profile?.name ?? "");
  const [location, setLocation] = useState(profile?.location ?? "");
  const [strategy, setStrategy] = useState(profile?.strategy ?? "balanced");
  const [phase, setPhase] = useState(profile?.phase ?? "");
  const [active, setActive] = useState(profile?.active ?? true);
  const [saving, setSaving] = useState(false);

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
    if (!name.trim() || (!isEdit && !location.trim())) return;

    setSaving(true);
    try {
      if (isEdit) {
        const result = await updateProfile(profile.id, {
          name: name.trim(),
          strategy,
          phase: phase || undefined,
          active,
        });
        if (result.ok && result.profile) {
          onSaved(result.profile);
        } else {
          addError(result.error || "Failed to update profile", "Cortex");
        }
      } else {
        const result = await createProfile({
          location: location.trim(),
          name: name.trim(),
          strategy,
          phase: phase || undefined,
        });
        if (result.ok && result.profile) {
          onSaved(result.profile);
        } else {
          addError(result.error || "Failed to create profile", "Cortex");
        }
      }
    } catch {
      addError("Failed to save profile", "Cortex");
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
          <h2 className="text-lg font-semibold">{isEdit ? "Edit Profile" : "Create Profile"}</h2>
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
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Name</label>
              <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="My Grow" required />
            </div>
            <div>
              <label className={labelClass}>Location</label>
              <input
                className={inputClass}
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="tent-1"
                required={!isEdit}
                disabled={isEdit}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Strategy</label>
              <select className={selectClass} value={strategy} onChange={(e) => setStrategy(e.target.value as GrowProfile["strategy"])}>
                {STRATEGIES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className={labelClass}>Phase</label>
              <select className={selectClass} value={phase} onChange={(e) => setPhase(e.target.value)}>
                <option value="">None</option>
                {PHASES.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
          </div>
          {isEdit && (
            <div>
              <label className={labelClass}>Active</label>
              <button
                type="button"
                onClick={() => setActive(!active)}
                className={`relative w-9 h-5 rounded-full transition-colors cursor-pointer mt-1 ${
                  active ? "bg-teal-500" : "bg-white/20"
                }`}
              >
                <span className={`absolute left-0 top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                  active ? "translate-x-[18px]" : "translate-x-0.5"
                }`} />
              </button>
            </div>
          )}
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
            disabled={saving || !name.trim() || (!isEdit && !location.trim())}
            className="px-4 py-1.5 text-sm rounded-lg bg-teal-500/80 text-white hover:bg-teal-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors cursor-pointer"
          >
            {saving ? "Saving..." : isEdit ? "Save Changes" : "Create Profile"}
          </button>
        </div>
      </form>
    </div>
  );
}