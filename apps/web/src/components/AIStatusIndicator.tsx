import React from "react";

type AIStatusIndicatorProps = {
  isActive: boolean;
  lastCommandTs: number | null;
};

export function AIStatusIndicator({ isActive, lastCommandTs }: AIStatusIndicatorProps): React.ReactElement {
  const formatTimeAgo = (ts: number): string => {
    const seconds = Math.floor((Date.now() - ts) / 1000);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  };

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-panel-bg/50 border border-panel-border">
      <div className="flex items-center gap-1.5">
        <svg
          className={`w-4 h-4 ${isActive ? "text-purple-500" : "text-gray-400"}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
          />
        </svg>
        <span className="text-xs font-medium">AI</span>
      </div>
      <div
        className={`w-2 h-2 rounded-full ${
          isActive ? "bg-purple-500 animate-pulse" : "bg-gray-400"
        }`}
      />
      <span className="text-xs opacity-60">
        {isActive
          ? lastCommandTs
            ? `Active (${formatTimeAgo(lastCommandTs)})`
            : "Active"
          : "Inactive"}
      </span>
    </div>
  );
}
