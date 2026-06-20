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
import { useRouter } from "next/navigation";
import type { TrendsResponse } from "@/lib/types/api";
import { colors } from "@/lib/colors";

interface TrendRow {
  date: string; // full YYYY-MM-DD, for navigation
  label: string; // short MM-DD, for the axis
  inRange: number;
  below: number;
  above: number;
}

export function TrendsChart({ data }: { data: TrendsResponse }) {
  const router = useRouter();
  const rows: TrendRow[] = data.points.map((p) => ({
    date: p.date,
    label: p.date.slice(5),
    inRange: p.in_range_pct,
    below: p.below_pct,
    above: p.above_pct,
  }));

  const goToDay = (state: unknown) => {
    const payload = (state as {
      activePayload?: Array<{ payload?: TrendRow }>;
    })?.activePayload;
    const date = payload?.[0]?.payload?.date;
    if (date) router.push(`/day/${date}`);
  };

  return (
    <ResponsiveContainer width="100%" height={360}>
      <AreaChart
        data={rows}
        onClick={goToDay}
        style={{ cursor: "pointer" }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="label" tick={{ fontSize: 11 }} />
        <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
        <Tooltip
          labelFormatter={(_, payload) =>
            payload?.[0]?.payload?.date ?? ""
          }
          formatter={(v) =>
            typeof v === "number" ? `${v.toFixed(1)}%` : String(v ?? "")
          }
        />
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
