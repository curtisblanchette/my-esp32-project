import React, { useEffect, useState } from "react";

import { fetchRules, fetchCortexStatus, type CortexRule, type RuleSuggestion, type Device, type CortexStatus } from "../api";
import { NerveCenterOverview } from "../components/NerveCenterOverview";
import { NerveCenterRules } from "../components/NerveCenterRules";
import { NerveCenterSuggestions } from "../components/NerveCenterSuggestions";
import { NerveCenterBaselines } from "../components/NerveCenterBaselines";

type Tab = "overview" | "rules" | "suggestions" | "baselines";

type NerveCenterProps = {
  devices: Device[];
  suggestions: RuleSuggestion[];
  rules: CortexRule[];
  setRules: React.Dispatch<React.SetStateAction<CortexRule[]>>;
  onResolveAdjustment: (id: string, action: "approve" | "reject") => Promise<void>;
  onRunAdvisor: () => Promise<void>;
  addError: (message: string, source?: string) => void;
};

const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "rules", label: "Rules" },
  { key: "suggestions", label: "Suggestions" },
  { key: "baselines", label: "Baselines" },
];

export function NerveCenter({
  devices,
  suggestions,
  rules,
  setRules,
  onResolveAdjustment,
  onRunAdvisor,
  addError,
}: NerveCenterProps): React.ReactElement {
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [status, setStatus] = useState<CortexStatus | null>(null);
  const [loading, setLoading] = useState(true);

  // Fetch rules and status on mount
  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      const results = await Promise.allSettled([
        fetchRules(controller.signal),
        fetchCortexStatus(controller.signal),
      ]);

      if (controller.signal.aborted) return;

      if (results[0].status === "fulfilled") {
        setRules(results[0].value);
      } else {
        console.warn("Failed to fetch rules:", results[0].reason);
      }

      if (results[1].status === "fulfilled") {
        setStatus(results[1].value);
      } else {
        console.warn("Failed to fetch status:", results[1].reason);
      }

      setLoading(false);
    }

    load();
    return () => controller.abort();
  }, [setRules, addError]);

  const pendingCount = suggestions.filter((s) => s.status === "pending").length;

  return (
    <div className="flex-1 w-full px-3 py-5 pb-40 md:px-5 md:pb-24">
      <div className="max-w-[900px] mx-auto space-y-5">
        {/* Tab bar */}
        <div className="flex gap-1 p-1 rounded-xl border border-panel-border bg-panel/30 backdrop-blur-[6px]">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex-1 px-3 py-2 text-sm font-medium rounded-lg transition-colors cursor-pointer ${
                activeTab === tab.key
                  ? "bg-white/10 text-white"
                  : "text-white/50 hover:text-white/70 hover:bg-white/5"
              }`}
            >
              {tab.label}
              {tab.key === "suggestions" && pendingCount > 0 && (
                <span className="ml-1.5 px-1.5 py-0.5 text-[10px] rounded-full bg-teal-500/20 text-teal-400">
                  {pendingCount}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {loading ? (
          <div className="text-sm opacity-50 text-center py-12">Loading...</div>
        ) : (
          <>
            {activeTab === "overview" && (
              <NerveCenterOverview
                status={status}
                rules={rules}
                suggestions={suggestions}
                onRunAdvisor={onRunAdvisor}
                onTabChange={setActiveTab}
              />
            )}
            {activeTab === "rules" && (
              <NerveCenterRules rules={rules} setRules={setRules} addError={addError} />
            )}
            {activeTab === "suggestions" && (
              <NerveCenterSuggestions
                suggestions={suggestions}
                onResolveAdjustment={onResolveAdjustment}
                onRunAdvisor={onRunAdvisor}
              />
            )}
            {activeTab === "baselines" && (
              <NerveCenterBaselines devices={devices} addError={addError} />
            )}
          </>
        )}
      </div>
    </div>
  );
}
