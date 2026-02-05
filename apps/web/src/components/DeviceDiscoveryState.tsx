import React from "react";

export function DeviceDiscoveryState(): React.ReactElement {
  return (
    <div className="flex-1 min-w-0 border border-panel-border rounded-2xl p-5 backdrop-blur-[10px] flex items-center justify-center min-h-[400px]">
      <div className="text-center">
        {/* Animated radar icon */}
        <div className="relative inline-flex items-center justify-center w-20 h-20 mb-4">
          <div className="absolute inset-0 rounded-full bg-blue-500/10 animate-radar-ping" />
          <div className="absolute inset-3 rounded-full bg-blue-500/15 animate-radar-ping [animation-delay:0.5s]" />
          <div className="relative z-10 w-12 h-12 rounded-full bg-blue-500/20 border border-blue-500/30 flex items-center justify-center">
            <svg
              className="w-6 h-6 text-blue-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M8.111 16.404a5.5 5.5 0 017.778 0M12 20h.01m-7.08-7.071c3.904-3.905 10.236-3.905 14.141 0M1.394 9.393c5.857-5.857 15.355-5.857 21.213 0"
              />
            </svg>
          </div>
        </div>
        <div className="text-sm font-medium text-blue-400">
          Discovering devices on the network
        </div>
        <div className="text-xs opacity-60 mt-1">
          Waiting for device registration...
        </div>
      </div>
    </div>
  );
}
