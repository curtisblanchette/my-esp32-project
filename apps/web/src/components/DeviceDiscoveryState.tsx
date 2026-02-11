import React from "react";

export function DeviceDiscoveryState(): React.ReactElement {
  return (
    <div className="flex-1 min-w-0 border border-panel-border rounded-2xl p-5 backdrop-blur-[10px] flex items-center justify-center min-h-[400px]">
      <div className="text-center">
        {/* Animated logo */}
        <div className="relative inline-flex items-center justify-center w-40 h-40 mb-4">
          <div className="absolute inset-0 rounded-full bg-white/5 animate-radar-ping" />
          <div className="absolute inset-5 rounded-full bg-white/8 animate-radar-ping [animation-delay:0.5s]" />
          <img
            src="/favicon.svg"
            alt="Mycelium"
            className="relative z-10 w-24 h-24 opacity-80 animate-pulse"
          />
        </div>
        <div className="text-sm font-medium opacity-80">
          Discovering devices on the network
        </div>
        <div className="text-xs opacity-60 mt-1">
          Waiting for device registration...
        </div>
      </div>
    </div>
  );
}
