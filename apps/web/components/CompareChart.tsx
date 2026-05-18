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
import { format, parseISO } from "date-fns";
import type { CompareResponse } from "@/lib/types/api";
import { colors } from "@/lib/colors";

function seriesToRows(
  points: CompareResponse["series_a"],
  key: string,
) {
  return points.map((p) => ({
    time: format(parseISO(p.timestamp), "HH:mm"),
    [key]: p.bg_mgdl,
  }));
}

export function CompareChart({ data }: { data: CompareResponse }) {
  const a = seriesToRows(data.series_a, "a");
  const b = seriesToRows(data.series_b, "b");
  const merged: Record<string, string | number>[] = [];
  const maxLen = Math.max(a.length, b.length);
  for (let i = 0; i < maxLen; i++) {
    merged.push({
      time: a[i]?.time ?? b[i]?.time ?? "",
      a: a[i]?.a,
      b: b[i]?.b,
    });
  }

  const { low, high } = data.bg_targets;

  return (
    <ResponsiveContainer width="100%" height={360}>
      <LineChart data={merged}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="time" tick={{ fontSize: 11 }} interval={23} />
        <YAxis domain={[40, 420]} tick={{ fontSize: 11 }} />
        <ReferenceLine y={high} stroke={colors.highLine} strokeDasharray="4 4" />
        <ReferenceLine y={low} stroke={colors.lowLine} strokeDasharray="4 4" />
        <Tooltip />
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
