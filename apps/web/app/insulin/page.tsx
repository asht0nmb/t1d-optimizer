"use client";

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { InsulinResponse } from "@/lib/types/api";
import { colors } from "@/lib/colors";

export default function InsulinPage() {
  const [data, setData] = useState<InsulinResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/insulin?days=30")
      .then((r) => r.json())
      .then((body) => {
        if (body.error) setError(body.error);
        else setData(body);
      });
  }, []);

  const rows =
    data?.days.map((d) => ({
      date: d.date.slice(5),
      bolus: d.bolus_units,
      basal: d.basal_units,
    })) ?? [];

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Insulin history</h1>
      {error && <p className="text-red-600">{error}</p>}
      {!data && !error && <p className="text-slate-500">Loading…</p>}
      {data && (
        <ResponsiveContainer width="100%" height={400}>
          <BarChart data={rows}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend />
            <Bar dataKey="bolus" stackId="a" fill={colors.bolus} name="Bolus" />
            <Bar dataKey="basal" stackId="a" fill={colors.basalEdge} name="Basal" />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
