import React, { useEffect, useState } from "react";
import { fetchHealth, fetchGoals, type GrowProfile, type HealthSnapshot } from "../api";

type OverviewProps = {
  profiles: GrowProfile[];
  onTabChange: (tab: "overview" | "goals" | "room") => void;
};

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

function healthColor(score: number): string {
  if (score >= 80) return "text-green-400";
  if (score >= 50) return "text-yellow-400";
  return "text-red-400";
}

function daysInPhase(phaseStart: string | null): string {
  if (!phaseStart) return "--";
  const start = new Date(phaseStart).getTime();
  const days = Math.floor((Date.now() - start) / 86_400_000);
  return `${days}d`;
}

function StatCard({ label, children }: { label: string; children: React.ReactNode }): React.ReactElement {
  return (
    <div className="rounded-xl border border-panel-border bg-panel/30 backdrop-blur-[6px] p-4 flex-1 min-w-[200px]">
      <div className="text-xs opacity-50 mb-2">{label}</div>
      {children}
    </div>
  );
}

export function NerveCenterOverview({ profiles, onTabChange }: OverviewProps): React.ReactElement {
  const [health, setHealth] = useState<HealthSnapshot | null>(null);
  const [goalCount, setGoalCount] = useState<number>(0);

  const activeProfile = profiles.find((p) => p.active) ?? profiles[0] ?? null;

  useEffect(() => {
    if (!activeProfile) return;
    const controller = new AbortController();

    async function load() {
      const [healthResult, goals] = await Promise.allSettled([
        fetchHealth(activeProfile!.location, undefined, controller.signal),
        fetchGoals(activeProfile!.id, undefined, controller.signal),
      ]);
      if (controller.signal.aborted) return;
      if (healthResult.status === "fulfilled") setHealth(healthResult.value.latest);
      if (goals.status === "fulfilled") setGoalCount(goals.value.length);
    }

    load();
    return () => controller.abort();
  }, [activeProfile?.id, activeProfile?.location]);

  if (!activeProfile) {
    return (
      <div className="text-sm opacity-50 text-center py-12">
        No profiles configured. Go to the Goals tab to create one.
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-3">
      {/* Health Score */}
      <StatCard label="Health Score">
        <div className={`text-3xl font-bold ${health ? healthColor(health.score) : "text-white/30"}`}>
          {health ? Math.round(health.score) : "--"}
        </div>
        <div className="text-xs opacity-40 mt-0.5">
          {health ? `as of ${new Date(health.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "No data yet"}
        </div>
      </StatCard>

      {/* Active Profile */}
      <StatCard label="Active Profile">
        <div className="text-lg font-semibold">{activeProfile.name}</div>
        <div className="flex items-center gap-2 mt-1.5">
          <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded border ${STRATEGY_COLORS[activeProfile.strategy] || ""}`}>
            {activeProfile.strategy}
          </span>
          {activeProfile.phase && (
            <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded border ${PHASE_COLORS[activeProfile.phase] || ""}`}>
              {activeProfile.phase}
            </span>
          )}
          {activeProfile.phaseStart && (
            <span className="text-[10px] opacity-40">
              {daysInPhase(activeProfile.phaseStart)} in phase
            </span>
          )}
        </div>
      </StatCard>

      {/* Goal Count */}
      <StatCard label="Goals">
        <div className="text-3xl font-bold">{goalCount}</div>
        <button
          onClick={() => onTabChange("goals")}
          className="text-xs text-teal-400 hover:text-teal-300 cursor-pointer transition-colors mt-1"
        >
          Manage goals
        </button>
      </StatCard>
    </div>
  );
}