"use client";
import { fetchJson } from "@/lib/fetch-json";

import { useCallback, useEffect, useState } from "react";
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
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";

export default function InsulinPage() {
  const [data, setData] = useState<InsulinResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await fetchJson<InsulinResponse>("/api/insulin?days=30");
      if (body.error) setError(body.error);
      else setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load insulin history");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const rows =
    data?.days.map((d) => ({
      date: d.date.slice(5),
      bolus: d.bolus_units,
      basal: d.basal_units,
    })) ?? [];

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Insulin history</h1>
      <div aria-live="polite" aria-busy={loading}>
        {loading ? (
          <Card className="p-4">
            <Skeleton className="h-96 w-full" />
          </Card>
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : data && rows.length === 0 ? (
          <EmptyState
            title="No insulin data"
            description="No daily insulin totals in the last 30 days."
          />
        ) : data ? (
          <Card className="p-4">
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
          </Card>
        ) : null}
      </div>
    </div>
  );
}
