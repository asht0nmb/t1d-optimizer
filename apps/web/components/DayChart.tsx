"use client";

import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DayViewResponse } from "@/lib/types/api";
import { bgSegmentColor, colors } from "@/lib/colors";
import { dayWindowUtc } from "@/lib/dates";
import {
  clipIntervalToWindow,
  siteIssueEndTs,
  snapIntervalToTimestamps,
} from "@/lib/overlays";
import {
  DAY_MINUTES,
  formatMinutesLabel,
  hourTicks,
  minutesSinceMidnight,
} from "@/lib/chart-time";

/**
 * Shared numeric x-axis props for all three day panels. Keying on
 * minutes-since-midnight (not "HH:mm" categories) makes gaps proportional
 * and aligns the CGM / bolus / basal panels vertically.
 */
const sharedXAxis = {
  dataKey: "minute",
  type: "number" as const,
  domain: [0, DAY_MINUTES] as [number, number],
  ticks: hourTicks(3),
  tickFormatter: formatMinutesLabel,
  tick: { fontSize: 11 },
  allowDecimals: false,
};

function toChartRows(data: DayViewResponse) {
  const cgmRows = data.cgm.map((p) => ({
    minute: minutesSinceMidnight(p.timestamp),
    bg: p.bg_mgdl,
    backfilled: p.backfilled,
  }));

  const bolusRows = data.bolus.map((b) => ({
    minute: minutesSinceMidnight(b.timestamp),
    units: b.insulin_units,
  }));

  const basalRows = data.basal.map((b) => ({
    minute: minutesSinceMidnight(b.timestamp),
    rate: b.commanded_rate,
  }));

  return { cgmRows, bolusRows, basalRows };
}

interface OverlayArea {
  x1: number;
  x2: number;
}

/**
 * cgm_gaps + site_issues intervals, clipped to the local-day window and
 * snapped to existing CGM timestamps, then projected onto the shared
 * minutes-since-midnight axis so ReferenceArea coordinates match the lines.
 * Open-ended gaps run to the end of the day; open-ended site issues shade
 * one hour from onset (mirrors the local shell).
 */
function buildOverlayAreas(data: DayViewResponse): {
  gaps: OverlayArea[];
  siteIssues: OverlayArea[];
} {
  const { since, until } = dayWindowUtc(data.date, data.timezone);
  const sinceIso = since.toISOString();
  const untilIso = until.toISOString();
  const cgmTimes = data.cgm.map((p) => p.timestamp);

  const snap = (start: string, end: string): OverlayArea | null => {
    const clipped = clipIntervalToWindow(start, end, sinceIso, untilIso);
    const snapped = clipped
      ? snapIntervalToTimestamps(clipped, cgmTimes)
      : null;
    if (!snapped) return null;
    return {
      x1: minutesSinceMidnight(snapped.x1),
      x2: minutesSinceMidnight(snapped.x2),
    };
  };

  const isArea = (a: OverlayArea | null): a is OverlayArea => a != null;

  const gaps = data.cgm_gaps
    .map((g) => snap(g.start_ts, g.end_ts ?? untilIso))
    .filter(isArea);

  const siteIssues = data.site_issues
    .map((s) => {
      const end = siteIssueEndTs(s.first_occlusion_ts, s.last_occlusion_ts);
      return end ? snap(s.first_occlusion_ts, end) : null;
    })
    .filter(isArea);

  return { gaps, siteIssues };
}

export function DayChart({ data }: { data: DayViewResponse }) {
  const { low, high } = data.bg_targets;
  const { cgmRows, bolusRows, basalRows } = toChartRows(data);
  const { gaps, siteIssues } = buildOverlayAreas(data);
  const s = data.summary;

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Time in range" value={`${s.tir_pct.toFixed(0)}%`} />
        <Stat
          label="Mean BG"
          value={s.mean_bg != null ? `${s.mean_bg.toFixed(0)} mg/dL` : "—"}
        />
        <Stat label="TDD" value={`${s.tdd_units.toFixed(1)} u`} />
        <Stat label="Alarms" value={String(s.alarm_count)} />
      </div>

      <Panel title="CGM">
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={cgmRows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis {...sharedXAxis} />
            <YAxis domain={[40, 420]} tick={{ fontSize: 11 }} />
            {gaps.map((a, i) => (
              <ReferenceArea
                key={`gap-${i}`}
                x1={a.x1}
                x2={a.x2}
                fill="#94A3B8"
                fillOpacity={0.2}
                strokeOpacity={0}
              />
            ))}
            {siteIssues.map((a, i) => (
              <ReferenceArea
                key={`site-${i}`}
                x1={a.x1}
                x2={a.x2}
                fill={colors.orange}
                fillOpacity={0.15}
                strokeOpacity={0}
              />
            ))}
            <ReferenceLine y={high} stroke={colors.highLine} strokeDasharray="4 4" />
            <ReferenceLine y={low} stroke={colors.lowLine} strokeDasharray="4 4" />
            <Tooltip
              labelFormatter={(v) => formatMinutesLabel(Number(v))}
              formatter={(value) => [`${Math.round(Number(value))} mg/dL`, "BG"]}
            />
            <Line
              type="monotone"
              dataKey="bg"
              stroke={colors.green}
              dot={(props) => {
                const { cx, cy, payload, key } = props;
                if (cx == null || cy == null || !payload) {
                  return <g key={key} />;
                }
                const fill = bgSegmentColor(payload.bg, low, high);
                return (
                  <circle
                    key={key}
                    cx={cx}
                    cy={cy}
                    r={3}
                    fill={fill}
                    stroke={payload.backfilled ? "#90A4AE" : fill}
                    strokeDasharray={payload.backfilled ? "2 2" : undefined}
                  />
                );
              }}
              strokeWidth={1.5}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="Bolus">
        <ResponsiveContainer width="100%" height={140}>
          <ComposedChart data={bolusRows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis {...sharedXAxis} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip
              labelFormatter={(v) => formatMinutesLabel(Number(v))}
              formatter={(value) => [`${Number(value).toFixed(2)} u`, "Bolus"]}
            />
            <Bar dataKey="units" fill={colors.bolus} radius={[2, 2, 0, 0]} />
          </ComposedChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="Basal rate (U/hr)">
        <ResponsiveContainer width="100%" height={140}>
          <ComposedChart data={basalRows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis {...sharedXAxis} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip
              labelFormatter={(v) => formatMinutesLabel(Number(v))}
              formatter={(value) => [`${Number(value).toFixed(2)} U/hr`, "Basal"]}
            />
            <Area
              type="stepAfter"
              dataKey="rate"
              fill={colors.basalFill}
              stroke={colors.basalEdge}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </Panel>

      {(data.alarms.length > 0 || data.cgm_gaps.length > 0) && (
        <Panel title="Events">
          <ul className="max-h-40 space-y-1 overflow-y-auto text-sm text-slate-700">
            {data.alarms.map((a, i) => (
              <li key={`a-${i}`}>
                {formatMinutesLabel(minutesSinceMidnight(a.timestamp))} —{" "}
                {a.alarm_name} ({a.action})
              </li>
            ))}
            {data.cgm_gaps.map((g, i) => (
              <li key={`g-${i}`} className="text-slate-500">
                CGM gap {formatMinutesLabel(minutesSinceMidnight(g.start_ts))}
                {g.end_ts
                  ? ` → ${formatMinutesLabel(minutesSinceMidnight(g.end_ts))}`
                  : " (ongoing)"}
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="text-lg font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold text-slate-800">{title}</h2>
      {children}
    </section>
  );
}
