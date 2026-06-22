"use client";
import { fetchJson } from "@/lib/fetch-json";

import { useCallback, useEffect, useState } from "react";
import { TrendsChart } from "@/components/TrendsChart";
import type { TrendsResponse } from "@/lib/types/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";

export default function TrendsPage() {
  const [days, setDays] = useState<7 | 14 | 30>(14);
  const [data, setData] = useState<TrendsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await fetchJson<TrendsResponse>(`/api/trends?days=${days}`);
      if (body.error) setError(body.error);
      else setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load trends");
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">TIR trends</h1>
      <div className="flex gap-2">
        {([7, 14, 30] as const).map((d) => (
          <Button
            key={d}
            type="button"
            size="sm"
            variant={days === d ? "default" : "outline"}
            onClick={() => setDays(d)}
          >
            {d}d
          </Button>
        ))}
      </div>
      <div aria-live="polite" aria-busy={loading}>
        {loading ? (
          <Card className="p-4">
            <Skeleton className="h-72 w-full" />
          </Card>
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : data && data.points.length === 0 ? (
          <EmptyState
            title="No trend data"
            description="No daily summaries in the selected window."
          />
        ) : data ? (
          <Card className="p-4">
            <TrendsChart data={data} />
          </Card>
        ) : null}
      </div>
    </div>
  );
}
