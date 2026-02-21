import React, { useEffect, useState } from "react";
import { fetchRoomConfig, type RoomConfig } from "../api";

type RoomProps = {
  addError: (message: string, source?: string) => void;
};

function SectionCard({ title, children }: { title: string; children: React.ReactNode }): React.ReactElement {
  return (
    <div className="rounded-xl border border-panel-border bg-panel/30 backdrop-blur-[6px] p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-3">{title}</h3>
      {children}
    </div>
  );
}

function KV({ label, value }: { label: string; value: string | number | undefined | null }): React.ReactElement | null {
  if (value == null) return null;
  return (
    <div className="flex justify-between py-1 border-b border-white/5 last:border-0">
      <span className="text-xs opacity-50">{label}</span>
      <span className="text-xs text-white/80">{String(value)}</span>
    </div>
  );
}

export function NerveCenterRoom({ addError }: RoomProps): React.ReactElement {
  const [config, setConfig] = useState<RoomConfig | null>(null);
  const [configured, setConfigured] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      try {
        const result = await fetchRoomConfig(controller.signal);
        if (!controller.signal.aborted) {
          setConfigured(result.configured);
          setConfig(result.config);
        }
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        addError("Failed to fetch room config", "Cortex");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    load();
    return () => controller.abort();
  }, [addError]);

  if (loading) {
    return <div className="text-sm opacity-50 text-center py-12">Loading room configuration...</div>;
  }

  if (!configured || !config) {
    return (
      <div className="text-sm opacity-50 text-center py-12">
        No room configuration loaded. Set <code className="text-white/60">ROOM_CONFIG_PATH</code> to a YAML file.
      </div>
    );
  }

  const space = config.space as Record<string, unknown> | undefined;
  const actuators = config.actuators as Record<string, Record<string, unknown>> | undefined;
  const plant = config.plant as Record<string, unknown> | undefined;
  const substrate = config.substrate as Record<string, unknown> | undefined;
  const ventilation = config.ventilation as Record<string, unknown> | undefined;
  const initial = config.initial_conditions as Record<string, unknown> | undefined;

  return (
    <div className="space-y-4">
      {/* Room name */}
      {config.name && (
        <div className="text-lg font-semibold">{String(config.name)}</div>
      )}

      {/* Space */}
      {space && (
        <SectionCard title="Space">
          <KV label="Floor Area" value={space.floor_area_m2 != null ? `${space.floor_area_m2} m²` : undefined} />
          <KV label="Volume" value={space.volume_m3 != null ? `${space.volume_m3} m³` : undefined} />
          <KV label="ACH Base" value={space.ach_base as number} />
          <KV label="ACH Max" value={space.ach_max as number} />
          <KV label="Pots" value={space.num_pots as number} />
          <KV label="Max PPFD" value={space.max_ppfd != null ? `${space.max_ppfd} umol/m²/s` : undefined} />
          <KV label="CO₂ Ambient" value={space.co2_ambient != null ? `${space.co2_ambient} ppm` : undefined} />
          <KV label="Humidity Ambient" value={space.h_ambient != null ? `${space.h_ambient}%` : undefined} />
        </SectionCard>
      )}

      {/* Actuators */}
      {actuators && (
        <SectionCard title="Actuators">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {Object.entries(actuators).map(([relay, a]) => (
              <div key={relay} className="px-3 py-2 rounded-lg bg-white/5 border border-white/5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium">{String(a.name || relay)}</span>
                  <span className="text-[10px] opacity-40">{relay}</span>
                </div>
                <div className="flex items-center gap-2 mt-1 text-[10px] opacity-40">
                  <span>{a.max_watts}W</span>
                  <span className={`px-1 py-0.5 rounded border ${
                    a.control_type === "variable"
                      ? "bg-purple-500/20 text-purple-400 border-purple-500/30"
                      : "bg-blue-500/20 text-blue-400 border-blue-500/30"
                  }`}>
                    {String(a.control_type)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {/* Plant */}
      {plant && (
        <SectionCard title="Plant">
          <KV label="Phase" value={plant.phase as string} />
          <KV label="LAI" value={plant.lai as number} />
          <KV label="Pmax" value={plant.pmax as number} />
        </SectionCard>
      )}

      {/* Substrate */}
      {substrate && (
        <SectionCard title="Substrate">
          <KV label="Medium" value={substrate.medium as string} />
          {substrate.container && (() => {
            const c = substrate.container as Record<string, unknown>;
            return (
              <>
                <KV label="Container" value={c.type as string} />
                {c.diameter_cm && <KV label="Diameter" value={`${c.diameter_cm} cm`} />}
                {c.length_cm && <KV label="Length" value={`${c.length_cm} cm`} />}
                {c.width_cm && <KV label="Width" value={`${c.width_cm} cm`} />}
                <KV label="Depth" value={c.depth_cm != null ? `${c.depth_cm} cm` : undefined} />
                {c.height_cm && <KV label="Height" value={`${c.height_cm} cm`} />}
              </>
            );
          })()}
        </SectionCard>
      )}

      {/* Ventilation */}
      {ventilation && (
        <SectionCard title="Ventilation">
          {ventilation.exhaust && (() => {
            const ex = ventilation.exhaust as Record<string, unknown>;
            return (
              <>
                <KV label="Rated CFM" value={ex.rated_cfm as number} />
                <KV label="Max Static Pressure" value={ex.max_static_pressure != null ? `${ex.max_static_pressure} inWC` : undefined} />
              </>
            );
          })()}
          {ventilation.duct && (() => {
            const d = ventilation.duct as Record<string, unknown>;
            return (
              <>
                <KV label="Duct Diameter" value={d.diameter_in != null ? `${d.diameter_in}"` : undefined} />
                <KV label="Duct Length" value={d.length_ft != null ? `${d.length_ft} ft` : undefined} />
                <KV label="Material" value={d.material as string} />
                <KV label="90° Elbows" value={d.elbows_90 as number} />
                <KV label="Carbon Filter" value={d.has_carbon_filter ? "Yes" : "No"} />
              </>
            );
          })()}
          <KV label="Passive ACH" value={ventilation.passive_ach as number} />
        </SectionCard>
      )}

      {/* Initial Conditions */}
      {initial && (
        <SectionCard title="Initial Conditions">
          <KV label="Temperature" value={initial.temperature != null ? `${initial.temperature}°C` : undefined} />
          <KV label="Humidity" value={initial.humidity != null ? `${initial.humidity}%` : undefined} />
          <KV label="CO₂" value={initial.co2 != null ? `${initial.co2} ppm` : undefined} />
          <KV
            label="Soil Moisture"
            value={
              initial.soil_moisture != null
                ? Array.isArray(initial.soil_moisture)
                  ? (initial.soil_moisture as number[]).map((v) => `${v}%`).join(", ")
                  : `${initial.soil_moisture}%`
                : undefined
            }
          />
        </SectionCard>
      )}
    </div>
  );
}