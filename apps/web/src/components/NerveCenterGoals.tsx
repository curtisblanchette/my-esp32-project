import React, { useEffect, useState } from "react";
import {
  fetchGoals,
  deleteGoal,
  deleteProfile,
  guessSensorUnit,
  type GrowProfile,
  type GrowGoal,
  type Device,
} from "../api";
import { ProfileFormModal } from "./ProfileFormModal";
import { GoalFormModal } from "./GoalFormModal";

type GoalsProps = {
  profiles: GrowProfile[];
  setProfiles: React.Dispatch<React.SetStateAction<GrowProfile[]>>;
  devices: Device[];
  addError: (message: string, source?: string) => void;
};

const PHASES = ["all", "seedling", "veg", "flower", "late_flower", "dry", "cure"] as const;

const STRATEGY_COLORS: Record<string, string> = {
  precision: "bg-red-500/20 text-red-400 border-red-500/30",
  balanced: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  efficiency: "bg-green-500/20 text-green-400 border-green-500/30",
};

const PHASE_COLORS: Record<string, string> = {
  seedling: "bg-lime-500/20 text-lime-400 border-lime-500/30",
  veg: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  flower: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  late_flower: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  dry: "bg-stone-500/20 text-stone-400 border-stone-500/30",
  cure: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
};

const METRIC_TYPE_COLORS: Record<string, string> = {
  sensor: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  derived: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  relay_schedule: "bg-amber-500/20 text-amber-400 border-amber-500/30",
};

export function NerveCenterGoals({ profiles, setProfiles, devices, addError }: GoalsProps): React.ReactElement {
  const [selectedProfileId, setSelectedProfileId] = useState<string>(
    profiles.find((p) => p.active)?.id ?? profiles[0]?.id ?? "",
  );
  const [goals, setGoals] = useState<GrowGoal[]>([]);
  const [phaseFilter, setPhaseFilter] = useState<string>("all");
  const [loading, setLoading] = useState(false);

  // Modals
  const [profileModalOpen, setProfileModalOpen] = useState(false);
  const [editingProfile, setEditingProfile] = useState<GrowProfile | null>(null);
  const [goalModalOpen, setGoalModalOpen] = useState(false);
  const [editingGoal, setEditingGoal] = useState<GrowGoal | null>(null);

  // Delete confirmation
  const [deletingGoalId, setDeletingGoalId] = useState<string | null>(null);
  const [deletingProfileId, setDeletingProfileId] = useState<string | null>(null);

  const selectedProfile = profiles.find((p) => p.id === selectedProfileId) ?? null;

  // Fetch goals when profile changes
  useEffect(() => {
    if (!selectedProfileId) return;
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      try {
        const g = await fetchGoals(selectedProfileId, undefined, controller.signal);
        if (!controller.signal.aborted) setGoals(g);
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        addError("Failed to fetch goals", "Cortex");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    load();
    return () => controller.abort();
  }, [selectedProfileId, addError]);

  // Sync selected profile when profiles change
  useEffect(() => {
    if (!selectedProfileId && profiles.length > 0) {
      setSelectedProfileId(profiles.find((p) => p.active)?.id ?? profiles[0].id);
    }
  }, [profiles, selectedProfileId]);

  const filteredGoals = phaseFilter === "all"
    ? goals
    : goals.filter((g) => g.phase === phaseFilter || g.phase === null);

  const handleDeleteGoal = async (id: string) => {
    if (deletingGoalId !== id) {
      setDeletingGoalId(id);
      return;
    }
    try {
      const ok = await deleteGoal(id);
      if (ok) setGoals((prev) => prev.filter((g) => g.id !== id));
    } catch {
      addError("Failed to delete goal", "Cortex");
    } finally {
      setDeletingGoalId(null);
    }
  };

  const handleDeleteProfile = async (id: string) => {
    if (deletingProfileId !== id) {
      setDeletingProfileId(id);
      return;
    }
    try {
      const ok = await deleteProfile(id);
      if (ok) {
        setProfiles((prev) => prev.filter((p) => p.id !== id));
        if (selectedProfileId === id) {
          const remaining = profiles.filter((p) => p.id !== id);
          setSelectedProfileId(remaining[0]?.id ?? "");
          setGoals([]);
        }
      }
    } catch {
      addError("Failed to delete profile", "Cortex");
    } finally {
      setDeletingProfileId(null);
    }
  };

  const handleProfileSaved = (profile: GrowProfile) => {
    if (editingProfile) {
      setProfiles((prev) => prev.map((p) => (p.id === profile.id ? profile : p)));
    } else {
      setProfiles((prev) => [...prev, profile]);
      setSelectedProfileId(profile.id);
    }
    setProfileModalOpen(false);
    setEditingProfile(null);
  };

  const handleGoalSaved = (goal: GrowGoal) => {
    if (editingGoal) {
      setGoals((prev) => prev.map((g) => (g.id === goal.id ? goal : g)));
    } else {
      setGoals((prev) => [...prev, goal]);
    }
    setGoalModalOpen(false);
    setEditingGoal(null);
  };

  return (
    <div className="space-y-4">
      {/* Profile selector bar */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1">
        {profiles.map((p) => {
          const isSelected = p.id === selectedProfileId;
          const isConfirmingDelete = deletingProfileId === p.id;
          return (
            <div key={p.id} className="flex-shrink-0 relative group">
              <button
                onClick={() => {
                  setSelectedProfileId(p.id);
                  setDeletingProfileId(null);
                }}
                className={`px-3 py-2 rounded-xl border transition-colors cursor-pointer text-left ${
                  isSelected
                    ? "border-white/20 bg-white/10"
                    : "border-panel-border bg-panel/30 hover:bg-white/5"
                }`}
              >
                <div className="text-sm font-medium">{p.name}</div>
                <div className="flex items-center gap-1.5 mt-1">
                  <span className={`px-1 py-0.5 text-[9px] font-medium rounded border ${STRATEGY_COLORS[p.strategy] || ""}`}>
                    {p.strategy}
                  </span>
                  {p.phase && (
                    <span className={`px-1 py-0.5 text-[9px] font-medium rounded border ${PHASE_COLORS[p.phase] || ""}`}>
                      {p.phase}
                    </span>
                  )}
                </div>
              </button>
              {/* Edit/delete on hover */}
              {isSelected && (
                <div className="absolute -top-1 -right-1 flex gap-0.5">
                  <button
                    onClick={() => {
                      setEditingProfile(p);
                      setProfileModalOpen(true);
                    }}
                    className="p-0.5 rounded bg-white/10 text-white/50 hover:text-white/80 transition-colors cursor-pointer"
                    aria-label="Edit profile"
                  >
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                    </svg>
                  </button>
                  <button
                    onClick={() => handleDeleteProfile(p.id)}
                    onBlur={() => setDeletingProfileId(null)}
                    className={`p-0.5 rounded transition-colors cursor-pointer ${
                      isConfirmingDelete
                        ? "bg-red-500/20 text-red-400"
                        : "bg-white/10 text-white/50 hover:text-red-400"
                    }`}
                    aria-label={isConfirmingDelete ? "Confirm delete" : "Delete profile"}
                  >
                    {isConfirmingDelete ? (
                      <span className="text-[9px] font-medium px-0.5">Del?</span>
                    ) : (
                      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    )}
                  </button>
                </div>
              )}
            </div>
          );
        })}
        <button
          onClick={() => {
            setEditingProfile(null);
            setProfileModalOpen(true);
          }}
          className="flex-shrink-0 px-3 py-2 rounded-xl border border-dashed border-panel-border text-white/40 hover:text-white/60 hover:border-white/20 transition-colors cursor-pointer text-sm"
        >
          + Create
        </button>
      </div>

      {/* Phase filter pills */}
      {selectedProfile && (
        <div className="flex gap-1.5 flex-wrap">
          {PHASES.map((phase) => (
            <button
              key={phase}
              onClick={() => setPhaseFilter(phase)}
              className={`px-2.5 py-1 text-xs rounded-lg border transition-colors cursor-pointer ${
                phaseFilter === phase
                  ? "border-white/20 bg-white/10 text-white"
                  : "border-panel-border text-white/40 hover:text-white/60"
              }`}
            >
              {phase === "all" ? "All" : phase}
            </button>
          ))}
        </div>
      )}

      {/* Goals */}
      {!selectedProfile ? (
        <div className="text-sm opacity-50 text-center py-8">
          Create a profile to start adding goals.
        </div>
      ) : loading ? (
        <div className="text-sm opacity-50 text-center py-8">Loading goals...</div>
      ) : (
        <>
          {filteredGoals.length === 0 ? (
            <div className="text-sm opacity-50 text-center py-8">
              No goals{phaseFilter !== "all" ? ` for phase "${phaseFilter}"` : ""}. Add one below.
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {filteredGoals.map((goal) => {
                const unit = guessSensorUnit(goal.metric);
                const isConfirmingDelete = deletingGoalId === goal.id;
                return (
                  <div
                    key={goal.id}
                    className="rounded-xl border border-panel-border bg-panel/30 backdrop-blur-[6px] p-4"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-semibold">{goal.metric}</span>
                        <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded border ${METRIC_TYPE_COLORS[goal.metricType] || ""}`}>
                          {goal.metricType}
                        </span>
                        {goal.phase && (
                          <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded border ${PHASE_COLORS[goal.phase] || ""}`}>
                            {goal.phase}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => {
                            setEditingGoal(goal);
                            setGoalModalOpen(true);
                          }}
                          className="p-1 rounded text-white/30 hover:text-white/70 hover:bg-white/5 transition-colors cursor-pointer"
                          aria-label="Edit goal"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                          </svg>
                        </button>
                        <button
                          onClick={() => handleDeleteGoal(goal.id)}
                          onBlur={() => setDeletingGoalId(null)}
                          className={`p-1 rounded transition-colors cursor-pointer ${
                            isConfirmingDelete
                              ? "text-red-400 bg-red-500/10"
                              : "text-white/30 hover:text-red-400 hover:bg-white/5"
                          }`}
                          aria-label={isConfirmingDelete ? "Confirm delete" : "Delete goal"}
                        >
                          {isConfirmingDelete ? (
                            <span className="text-[10px] font-medium px-0.5">Confirm?</span>
                          ) : (
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          )}
                        </button>
                      </div>
                    </div>

                    {/* Range */}
                    <div className="text-xs mt-2 opacity-60">
                      Range:{" "}
                      <span className="text-white/80">
                        {goal.rangeMin != null ? `${goal.rangeMin}${unit}` : "--"} &ndash;{" "}
                        {goal.rangeMax != null ? `${goal.rangeMax}${unit}` : "--"}
                      </span>
                    </div>

                    {/* Tolerance + Priority */}
                    <div className="flex items-center gap-3 text-[10px] opacity-40 mt-1">
                      <span>tolerance: {goal.tolerance}{unit}</span>
                      <span>priority: {goal.priority}</span>
                      {goal.timeWindow && (
                        <span>{goal.timeWindow.onHour}:00&ndash;{goal.timeWindow.offHour}:00</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Add Goal button */}
          <button
            onClick={() => {
              setEditingGoal(null);
              setGoalModalOpen(true);
            }}
            className="w-full py-2 rounded-xl border border-dashed border-panel-border text-white/40 hover:text-white/60 hover:border-white/20 transition-colors cursor-pointer text-sm"
          >
            + Add Goal
          </button>
        </>
      )}

      {/* Modals */}
      {profileModalOpen && (
        <ProfileFormModal
          profile={editingProfile}
          onClose={() => { setProfileModalOpen(false); setEditingProfile(null); }}
          onSaved={handleProfileSaved}
          addError={addError}
        />
      )}
      {goalModalOpen && selectedProfile && (
        <GoalFormModal
          profileId={selectedProfile.id}
          goal={editingGoal}
          onClose={() => { setGoalModalOpen(false); setEditingGoal(null); }}
          onSaved={handleGoalSaved}
          addError={addError}
          devices={devices}
        />
      )}
    </div>
  );
}