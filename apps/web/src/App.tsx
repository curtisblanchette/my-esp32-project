import React, { useEffect, useMemo, useState } from 'react';

import { fetchHistory, fetchLatest, type HistoryPoint, type LatestReading } from './api';
import { TimelineChart } from './components/TimelineChart';

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

function fmt1(n: number): string {
  return Number.isFinite(n) ? n.toFixed(1) : '--';
}

function tempToMix(tC: number): number {
  const minC = 8;
  const maxC = 30;
  const x = clamp((tC - minC) / (maxC - minC), 0, 1);
  const eased = Math.pow(x, 0.65);
  return clamp(0.12 + eased * 0.88, 0, 1);
}

function fmtTime(ms: number): string {
  return new Date(ms).toLocaleString();
}

function fmtTimeShort(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

enum DateRangePreset {
  ONE_HOUR = '1h',
  SIX_HOUR = '6h',
  TWELVE_HOUR = '12h',
  TWENTY_FOUR_HOUR = '24h',
  SEVEN_DAY = '7d',
  THIRTY_DAY = '30d',
  CUSTOM = 'custom'
}
const dateRangePresets = Object.values(DateRangePreset);

function getPresetRange(preset: DateRangePreset, customStart?: number, customEnd?: number): {
  sinceMs: number;
  untilMs: number
} {
  const now = Date.now();
  if (preset === 'custom' && customStart && customEnd) {
    return { sinceMs: customStart, untilMs: customEnd };
  }
  const ranges: Record<Exclude<DateRangePreset, 'custom'>, number> = {
    [DateRangePreset.ONE_HOUR]: 60 * 60 * 1000,
    [DateRangePreset.SIX_HOUR]: 6 * 60 * 60 * 1000,
    [DateRangePreset.TWELVE_HOUR]: 12 * 60 * 60 * 1000,
    [DateRangePreset.TWENTY_FOUR_HOUR]: 24 * 60 * 60 * 1000,
    [DateRangePreset.SEVEN_DAY]: 7 * 24 * 60 * 60 * 1000,
    [DateRangePreset.THIRTY_DAY]: 30 * 24 * 60 * 60 * 1000,
  };
  const duration = ranges[preset as Exclude<DateRangePreset, 'custom'>] || ranges['6h'];
  return { sinceMs: now - duration, untilMs: now };
}

function formatDateForInput(ms: number): string {
  const d = new Date(ms);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hours = String(d.getHours()).padStart(2, '0');
  const minutes = String(d.getMinutes()).padStart(2, '0');
  return `${ year }-${ month }-${ day }T${ hours }:${ minutes }`;
}

export function App(): React.ReactElement {
  const [latest, setLatest] = useState<LatestReading | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [historySub, setHistorySub] = useState<string>('--');
  const [dateRangePreset, setDateRangePreset] = useState<DateRangePreset>(DateRangePreset.ONE_HOUR);
  const [customStartMs, setCustomStartMs] = useState<number>(Date.now() - 6 * 60 * 60 * 1000);
  const [customEndMs, setCustomEndMs] = useState<number>(Date.now());
  const [timeRangeBounds, setTimeRangeBounds] = useState<{
    sinceMs: number;
    untilMs: number
  }>({ sinceMs: Date.now() - 6 * 60 * 60 * 1000, untilMs: Date.now() });

  useEffect(() => {
    // AbortController is used to cancel the fetch if the component unmounts
    const controller = new AbortController();

    async function refresh() {
      try {
        const l = await fetchLatest(controller.signal);
        setLatest(l);
      } catch {
        // ignore
      }
    }

    refresh();

    // refresh every 2 seconds
    const t = window.setInterval(refresh, 2000);

    return () => {
      controller.abort();
      window.clearInterval(t);
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    async function refresh() {
      try {
        const { sinceMs, untilMs } = getPresetRange(dateRangePreset, customStartMs, customEndMs);
        setTimeRangeBounds({ sinceMs, untilMs });
        const points = await fetchHistory({
          sinceMs,
          untilMs,
          limit: 800,
          bucketMs: 60_000,
          signal: controller.signal
        });
        setHistory(points);

        if (points.length === 0) {
          setHistorySub('No history yet');
          return;
        }

        const firstTs = Number(points[0]!.ts);
        const lastTs = Number(points[points.length - 1]!.ts);
        setHistorySub(`${ points.length } points | ${ fmtTimeShort(firstTs) } → ${ fmtTimeShort(lastTs) }`);
      } catch {
        setHistorySub('Error loading history');
      }
    }

    refresh();
    const t = window.setInterval(refresh, 10000);

    return () => {
      controller.abort();
      window.clearInterval(t);
    };
  }, [
    dateRangePreset,
    customStartMs,
    customEndMs
  ]);

  const derived = useMemo(() => {
    if (!latest) return null;
    const t = Number(latest.temp);
    const h = clamp(Number(latest.humidity), 0, 100);
    const mix = tempToMix(t);

    const humiditySub = h >= 70 ? 'Humid' : h <= 35 ? 'Dry' : 'Comfortable';
    const tempNote = t >= 25 ? 'Warm' : t <= 18 ? 'Cool' : 'Comfortable';
    const humidityNote = h >= 70 ? 'Air feels heavy' : h <= 35 ? 'Consider a humidifier' : 'Nice range';

    return { t, h, mix, humiditySub, tempNote, humidityNote };
  }, [latest]);

  const getButtonGroupClass = (index: number) => {
      if (index === 0) {
        return 'px-4 py-2 border rounded-l-md text-xs font-medium cursor-pointer transition-all duration-200 '
      } else if (index === dateRangePresets.length - 1) {
        return 'px-4 py-2 border rounded-r-md text-xs font-medium cursor-pointer transition-all duration-200 '
      } else {
        return 'px-4 py-2 border rounded-none text-xs font-medium cursor-pointer transition-all duration-200 '
      }
  };

  return (
    <div
      className="w-full max-w-[1040px] border border-panel-border rounded-2xl p-5 bg-black/[0.03] backdrop-blur-[10px]">
      <h1 className="m-0 mb-3 text-xl">Sensor Dashboard</h1>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(280px,100%),1fr))] gap-3 mt-3">
        <div className="rounded-2xl border border-panel-border bg-panel p-4 min-w-0 [container-type:inline-size]">
          <div className="flex items-baseline justify-between gap-3 mb-3">
            <div className="opacity-80 text-xs tracking-wider">Temperature</div>
            <div className="opacity-75 text-xs">Cool → Warm</div>
          </div>

          <div className="circle tempCircle rounded-full mx-auto relative grid place-items-center isolate"
               style={ { ['--t' as never]: derived?.mix ?? 0.5 } }>
            <div className="tempGlow" aria-hidden="true"/>
            <div className="relative z-[2] text-center px-3">
              <div className="readoutBig font-extrabold leading-none tracking-tight">
                <span className="readoutPill">{ derived ? fmt1(derived.t) : '--' }</span>
              </div>
              <div className="absolute top-0 right-2.5 text-sm font-bold opacity-90">°C</div>
              <div className="mt-2.5 text-xs opacity-[0.78]">{ derived ? derived.tempNote : 'Waiting…' }</div>
            </div>
          </div>
        </div>

        <div className="rounded-2xl border border-panel-border bg-panel p-4 min-w-0 [container-type:inline-size]">
          <div className="flex items-baseline justify-between gap-3 mb-3">
            <div className="opacity-80 text-xs tracking-wider">Relative Humidity</div>
            <div className="opacity-75 text-xs">{ derived ? derived.humiditySub : '--' }</div>
          </div>

          <div className="circle humidityCircle rounded-full mx-auto relative grid place-items-center isolate"
               style={ { ['--h' as never]: derived?.h ?? 0 } }>
            <div className="absolute rounded-full overflow-hidden z-[1] inset-[10px]" aria-hidden="true">
              <div className="waterFill"/>
            </div>
            <div className="relative z-[2] text-center px-3">
              <div className="readoutBig font-extrabold leading-none tracking-tight">
                <span className="readoutPill">{ derived ? fmt1(derived.h) : '--' }</span>
              </div>
              <div className="absolute top-0 right-2.5 text-sm font-bold opacity-90">%</div>
              <div className="mt-2.5 text-xs opacity-[0.78]">{ derived ? derived.humidityNote : 'Waiting…' }</div>
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-panel-border bg-panel p-4 min-w-0 mt-3">
        <div className="flex items-baseline justify-between gap-3 mb-3">
          <div className="opacity-80 text-xs tracking-wider">Timeline</div>
          <div className="opacity-75 text-xs">{ historySub }</div>
        </div>

        <div className="mb-4">
          <div className="flex mb-3 justify-end">
            { dateRangePresets.map((preset, index, array) => (
              <button
                key={ preset }
                className={ `${ getButtonGroupClass(index) } ${ dateRangePreset === preset
                  ? 'bg-blue-500/20 border-blue-500/50 text-blue-500 font-semibold'
                  : 'border-panel-border bg-gray-500/[0.08] hover:bg-gray-500/[0.15] hover:border-gray-500/40' }` }
                onClick={ () => setDateRangePreset(preset) }
              >
                { preset === 'custom' ? 'Custom' : preset.toUpperCase() }
              </button>
            )) }
          </div>
          { dateRangePreset === 'custom' && (
            <div className="flex flex-wrap gap-3 p-3 rounded-lg bg-gray-500/[0.08]">
              <label className="flex flex-col gap-1.5 flex-1 min-w-[200px]">
                <span className="text-xs font-medium opacity-80">From:</span>
                <input
                  type="datetime-local"
                  className="px-3 py-2 border border-panel-border rounded-md bg-white/5 text-inherit text-[13px] font-[inherit] focus:outline-none focus:border-blue-500/50 focus:bg-white/[0.08]"
                  value={ formatDateForInput(customStartMs) }
                  onChange={ (e) => setCustomStartMs(new Date(e.target.value).getTime()) }
                />
              </label>
              <label className="flex flex-col gap-1.5 flex-1 min-w-[200px]">
                <span className="text-xs font-medium opacity-80">To:</span>
                <input
                  type="datetime-local"
                  className="px-3 py-2 border border-panel-border rounded-md bg-white/5 text-inherit text-[13px] font-[inherit] focus:outline-none focus:border-blue-500/50 focus:bg-white/[0.08]"
                  value={ formatDateForInput(customEndMs) }
                  onChange={ (e) => setCustomEndMs(new Date(e.target.value).getTime()) }
                />
              </label>
            </div>
          ) }
        </div>

        <div className="relative w-full h-[clamp(220px,38vh,360px)]">
          <TimelineChart points={ history } timeRange={ timeRangeBounds }/>
        </div>
      </div>

      <div className="mt-3 text-xs opacity-75">
        { latest ? `Last update: ${ fmtTime(latest.updatedAt) }` : 'Waiting for first reading...' }
      </div>
    </div>
  );
}
