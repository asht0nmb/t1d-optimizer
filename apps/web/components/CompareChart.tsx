"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CompareResponse } from "@/lib/types/api";
import { colors } from "@/lib/colors";
import {
  alignCompareSeries,
  DAY_MINUTES,
  formatMinutesLabel,
  hourTicks,
} from "@/lib/chart-time";

export function CompareChart({ data }: { data: CompareResponse }) {
  const rows = alignCompareSeries(data.series_a, data.series_b);
  const { low, high } = data.bg_targets;

  return (
    <ResponsiveContainer width="100%" height={360}>
      <LineChart data={rows}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis
          dataKey="minute"
          type="number"
          domain={[0, DAY_MINUTES]}
          ticks={hourTicks(3)}
          tickFormatter={formatMinutesLabel}
          tick={{ fontSize: 11 }}
        />
        <YAxis domain={[40, 420]} tick={{ fontSize: 11 }} />
        <ReferenceLine y={high} stroke={colors.highLine} strokeDasharray="4 4" />
        <ReferenceLine y={low} stroke={colors.lowLine} strokeDasharray="4 4" />
        <Tooltip
          labelFormatter={(v) => formatMinutesLabel(Number(v))}
          formatter={(value) => [`${Math.round(Number(value))} mg/dL`, ""]}
        />
        <Legend />
        <Line
          type="monotone"
          dataKey="a"
          stroke={colors.bolus}
          dot={false}
          name={data.date_a}
          connectNulls
        />
        <Line
          type="monotone"
          dataKey="b"
          stroke={colors.orange}
          dot={false}
          name={data.date_b}
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
