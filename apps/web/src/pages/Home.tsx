import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { logObservation, type Device } from "../api";
import { ChatInput } from "../components/ChatInput";
import { ObservationForm } from "../components/ObservationForm";

type HomeProps = {
  devices: Device[];
};

export function Home({ devices }: HomeProps): React.ReactElement {
  const navigate = useNavigate();
  const [showObservation, setShowObservation] = useState(false);

  return (
    <div className="flex-1 flex items-center justify-center px-4">
      <div className="w-full max-w-[600px] flex flex-col items-center gap-16">
        <img src="/logo_vertical.svg" alt="Mycelium" className="h-64 select-none opacity-75" draggable={false} />

        <div className="w-full rounded-xl border border-panel-border backdrop-blur-[10px] overflow-hidden">
          {showObservation ? (
            <ObservationForm
              devices={devices}
              onSubmit={async (obs) => {
                await logObservation(obs);
                setShowObservation(false);
              }}
              onCancel={() => setShowObservation(false)}
            />
          ) : (
            <ChatInput noBorder />
          )}

          <div className="flex items-stretch border-t border-panel-border">
            <button
              onClick={() => navigate("/devices")}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-panel/60 hover:bg-panel transition-colors cursor-pointer text-sm opacity-60 hover:opacity-80"
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="4" y="4" width="16" height="16" rx="2" />
                <rect x="9" y="9" width="6" height="6" />
                <path d="M15 2v2" /><path d="M15 20v2" /><path d="M2 15h2" /><path d="M2 9h2" />
                <path d="M20 15h2" /><path d="M20 9h2" /><path d="M9 2v2" /><path d="M9 20v2" />
              </svg>
              Devices
            </button>
            <span className="w-px bg-panel-border" />
            <button
              onClick={() => navigate("/nerve-center")}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-panel/60 hover:bg-panel transition-colors cursor-pointer text-sm opacity-60 hover:opacity-80"
            >
              <img src="/nerve_center.svg" alt="" width="16" height="16" className="opacity-80" />
              Nerve Center
            </button>
            <span className="w-px bg-panel-border" />
            <button
              onClick={() => setShowObservation((prev) => !prev)}
              className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 transition-colors cursor-pointer text-sm ${
                showObservation
                  ? "bg-amber-500/20 opacity-80"
                  : "bg-panel/60 hover:bg-panel opacity-60 hover:opacity-80"
              }`}
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
              Log Observation
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}