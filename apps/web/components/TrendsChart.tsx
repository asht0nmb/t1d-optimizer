"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendsResponse } from "@/lib/types/api";
import { colors } from "@/lib/colors";

export function TrendsChart({ data }: { data: TrendsResponse }) {
  const rows = data.points.map((p) => ({
    date: p.date.slice(5),
    inRange: p.in_range_pct,
    below: p.below_pct,
    above: p.above_pct,
  }));

  return (
    <ResponsiveContainer width="100%" height={360}>
      <AreaChart data={rows}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="date" tick={{ fontSize: 11 }} />
        <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
        <Tooltip formatter={(v: number) => `${v.toFixed(1)}%`} />
        <Legend />
        <Area
          type="monotone"
          dataKey="below"
          stackId="1"
          stroke={colors.red}
          fill={colors.red}
          fillOpacity={0.5}
          name="Below"
        />
        <Area
          type="monotone"
          dataKey="inRange"
          stackId="1"
          stroke={colors.green}
          fill={colors.green}
          fillOpacity={0.6}
          name="In range"
        />
        <Area
          type="monotone"
          dataKey="above"
          stackId="1"
          stroke={colors.orange}
          fill={colors.orange}
          fillOpacity={0.5}
          name="Above"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
