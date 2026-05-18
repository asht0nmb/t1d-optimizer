"use client";

import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { format, parseISO } from "date-fns";
import type { DayViewResponse } from "@/lib/types/api";
import { bgSegmentColor, colors } from "@/lib/colors";

function timeLabel(iso: string): string {
  return format(parseISO(iso), "HH:mm");
}

function toChartRows(data: DayViewResponse) {
  const cgmRows = data.cgm.map((p) => ({
    time: timeLabel(p.timestamp),
    bg: p.bg_mgdl,
    backfilled: p.backfilled,
    kind: "cgm" as const,
  }));

  const bolusRows = data.bolus.map((b) => ({
    time: timeLabel(b.timestamp),
    units: b.insulin_units,
    kind: "bolus" as const,
  }));

  const basalRows = data.basal.map((b) => ({
    time: timeLabel(b.timestamp),
    rate: b.commanded_rate,
    kind: "basal" as const,
  }));

  return { cgmRows, bolusRows, basalRows };
}

export function DayChart({ data }: { data: DayViewResponse }) {
  const { low, high } = data.bg_targets;
  const { cgmRows, bolusRows, basalRows } = toChartRows(data);
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
            <XAxis dataKey="time" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
            <YAxis domain={[40, 420]} tick={{ fontSize: 11 }} />
            <ReferenceLine y={high} stroke={colors.highLine} strokeDasharray="4 4" />
            <ReferenceLine y={low} stroke={colors.lowLine} strokeDasharray="4 4" />
            <Tooltip />
            <Line
              type="monotone"
              dataKey="bg"
              stroke={colors.green}
              dot={(props) => {
                const { cx, cy, payload } = props;
                if (cx == null || cy == null || !payload) return null;
                const fill = bgSegmentColor(payload.bg, low, high);
                return (
                  <circle
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
          <ComposedChart data={bolusRows}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="time" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Bar dataKey="units" fill={colors.bolus} radius={[2, 2, 0, 0]} />
          </ComposedChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="Basal rate (U/hr)">
        <ResponsiveContainer width="100%" height={140}>
          <ComposedChart data={basalRows}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="time" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
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
                {timeLabel(a.timestamp)} — {a.alarm_name} ({a.action})
              </li>
            ))}
            {data.cgm_gaps.map((g, i) => (
              <li key={`g-${i}`} className="text-slate-500">
                CGM gap {timeLabel(g.start_ts)}
                {g.end_ts ? ` → ${timeLabel(g.end_ts)}` : " (ongoing)"}
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
