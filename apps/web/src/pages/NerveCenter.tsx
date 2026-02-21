import React, { useEffect, useState } from "react";

import { fetchProfiles, type GrowProfile, type Device } from "../api";
import { NerveCenterOverview } from "../components/NerveCenterOverview";
import { NerveCenterGoals } from "../components/NerveCenterGoals";
import { NerveCenterRoom } from "../components/NerveCenterRoom";

type Tab = "overview" | "goals" | "room";

type NerveCenterProps = {
  devices: Device[];
  addError: (message: string, source?: string) => void;
};

const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "goals", label: "Goals" },
  { key: "room", label: "Room" },
];

export function NerveCenter({
  devices,
  addError,
}: NerveCenterProps): React.ReactElement {
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [profiles, setProfiles] = useState<GrowProfile[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      try {
        const p = await fetchProfiles(controller.signal);
        if (!controller.signal.aborted) setProfiles(p);
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        console.warn("Failed to fetch profiles:", err);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    load();
    return () => controller.abort();
  }, []);

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
                profiles={profiles}
                onTabChange={setActiveTab}
              />
            )}
            {activeTab === "goals" && (
              <NerveCenterGoals
                profiles={profiles}
                setProfiles={setProfiles}
                devices={devices}
                addError={addError}
              />
            )}
            {activeTab === "room" && (
              <NerveCenterRoom addError={addError} />
            )}
          </>
        )}
      </div>
    </div>
  );
}