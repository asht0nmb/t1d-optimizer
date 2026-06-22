"use client";
import { fetchJson } from "@/lib/fetch-json";

import { useCallback, useEffect, useState } from "react";
import { subDays, format } from "date-fns";
import { HeatmapGrid } from "@/components/HeatmapGrid";
import type { BgTargets, ConfigResponse, HeatmapResponse } from "@/lib/types/api";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";

export default function HeatmapPage() {
  const [data, setData] = useState<HeatmapResponse | null>(null);
  const [targets, setTargets] = useState<BgTargets | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const config = (await fetchJson("/api/config")) as ConfigResponse;
      setTargets(config.bg_targets);
      const to = config.date_bounds?.max_date ?? format(new Date(), "yyyy-MM-dd");
      const fromCandidate = format(subDays(new Date(to), 30), "yyyy-MM-dd");
      const from = config.date_bounds
        ? fromCandidate < config.date_bounds.min_date
          ? config.date_bounds.min_date
          : fromCandidate
        : fromCandidate;
      const heatmap = await fetchJson<HeatmapResponse>(`/api/heatmap?from=${from}&to=${to}`);
      if (heatmap.error) setError(heatmap.error);
      else setData(heatmap);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load heatmap");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">BG heatmap</h1>
      <p className="text-sm text-muted-foreground">
        Median BG by hour-of-day (last 30 days), anchored to your low/high
        targets.
      </p>
      <div aria-live="polite" aria-busy={loading}>
        {loading ? (
          <Card className="p-4">
            <Skeleton className="h-80 w-full" />
          </Card>
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : data && targets && data.cells.length === 0 ? (
          <EmptyState
            title="No heatmap data"
            description="No CGM readings in the selected range."
          />
        ) : data && targets ? (
          <Card className="p-4">
            <HeatmapGrid data={data} low={targets.low} high={targets.high} />
          </Card>
        ) : null}
      </div>
    </div>
  );
}
