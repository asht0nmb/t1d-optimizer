"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { AgpResponse } from "@/lib/types/api";
import { colors } from "@/lib/colors";

function tooltipValue(v: unknown): string {
  if (Array.isArray(v)) {
    return `${Number(v[0]).toFixed(0)}–${Number(v[1]).toFixed(0)}`;
  }
  if (typeof v === "number") return v.toFixed(0);
  return String(v ?? "");
}

export function AgpChart({ data }: { data: AgpResponse }) {
  const { low, high } = data.bg_targets;
  const rows = data.hours.map((h) => ({
    hour: h.hour,
    median: h.p50,
    outer: h.p05 != null && h.p95 != null ? [h.p05, h.p95] : null,
    iqr: h.p25 != null && h.p75 != null ? [h.p25, h.p75] : null,
  }));

  return (
    <ResponsiveContainer width="100%" height={400}>
      <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis
          dataKey="hour"
          type="number"
          domain={[0, 23]}
          ticks={[0, 3, 6, 9, 12, 15, 18, 21, 23]}
          tick={{ fontSize: 11 }}
        />
        <YAxis domain={[40, 300]} tick={{ fontSize: 11 }} />
        <ReferenceLine y={high} stroke={colors.highLine} strokeDasharray="4 4" />
        <ReferenceLine y={low} stroke={colors.lowLine} strokeDasharray="4 4" />
        <Tooltip formatter={tooltipValue} labelFormatter={(h) => `Hour ${h}`} />
        <Legend />
        <Area
          dataKey="outer"
          name="5–95%"
          stroke="none"
          fill={colors.bolus}
          fillOpacity={0.12}
          isAnimationActive={false}
        />
        <Area
          dataKey="iqr"
          name="25–75%"
          stroke="none"
          fill={colors.bolus}
          fillOpacity={0.25}
          isAnimationActive={false}
        />
        <Line
          dataKey="median"
          name="Median"
          stroke={colors.bolus}
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
